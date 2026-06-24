#!/usr/bin/env node
/**
 * VIDEO-FACTORY 核心引擎 — live-server.js
 * ========================================
 * AI带货视频生成系统后端服务，零依赖纯Node.js实现。
 *
 * 功能模块:
 *   1. HTTP服务层   — API路由 + 静态文件服务 + 全CORS
 *   2. 任务调度     — 异步后台执行 + jobId轮询
 *   3. 产品洞察     — hfsy网关gpt-5.5看图 + DeepSeek结构化分析
 *   4. 分镜脚本     — DeepSeek生成N段分镜(9字段故事板)
 *   5. 图片生成     — hfsy网关gpt-image-2 + 图生图 + 多模型降级
 *   6. 视频生成     — 丽帧API + Agnes回退 + 状态轮询
 *   7. ffmpeg拼接   — 多段concat + scale/crop 720x1280竖屏
 *   8. 作品存档     — .uvg-out/works.json 历史记录
 *
 * 端口: 8088
 * 作者: VIDEO-FACTORY Team
 */

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const { URL } = require('url');

// ═══════════════════════════════════════════════════════════
// 全局配置
// ═══════════════════════════════════════════════════════════

/** @type {number} 服务监听端口 */
const PORT = 8088;
/** @type {string} 输出文件根目录 */
const OUT_DIR = path.join(process.cwd(), '.uvg-out');
/** @type {string} 作品存档文件路径 */
const WORKS_FILE = path.join(OUT_DIR, 'works.json');
/** @type {number} HTTP请求超时(毫秒) */
const REQ_TIMEOUT_MS = 120000;
/** @type {number} 图片生成超时(毫秒) */
const IMAGE_TIMEOUT_MS = 120000;
/** @type {number} 视频生成轮询间隔(毫秒) */
const POLL_INTERVAL_MS = 5000;
/** @type {number} 视频生成最大轮询次数 */
const MAX_POLL_COUNT = 60;
/** @type {number} 分镜默认段数 */
const DEFAULT_SEGMENTS = 4;

// API密钥（从环境变量读取）
const CONFIG = {
  KUAIZI_API_KEY:   process.env.KUAIZI_API_KEY   || '',   // 丽帧API密钥
  IMAGE_API_KEY:    process.env.IMAGE_API_KEY    || '',   // hfsy图片网关密钥
  DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY || '',   // DeepSeek密钥
  AGNES_API_KEY:    process.env.AGNES_API_KEY    || '',   // Agnes回退密钥
  KIMI_API_KEY:     process.env.KIMI_API_KEY     || '',   // Kimi备用密钥
  VIDEO_BACKEND:    process.env.VIDEO_BACKEND    || 'kuaizi', // 视频后端: kuaizi | agnes
  DEEPSEEK_MODEL:   process.env.DEEPSEEK_MODEL   || 'deepseek-v4-pro', // DeepSeek模型
};

// API端点配置
const ENDPOINTS = {
  // hfsy网关 — GPT-5.5看图 + gpt-image-2生图
  HFSY_CHAT:  'https://www.hfsyapi.cn/v1/chat/completions',
  HFSY_IMAGE: 'https://www.hfsyapi.cn/v1/images/generations',
  // DeepSeek — 结构化分析 + 分镜脚本
  DEEPSEEK:   'https://api.deepseek.com/v1/chat/completions',
  // 丽帧 — 主视频生成
  KUAIZI_CREATE: 'https://aiopenapi.kuaizi.cn/ai-open-platform-api/v1/lz/video/task/create',
  KUAIZI_STATUS: 'https://aiopenapi.kuaizi.cn/ai-open-platform-api/v1/lz/video/task/status',
  // Agnes — 视频生成回退
  AGNES:      'https://apihub.agnes-ai.com/v1/videos',
};

// ═══════════════════════════════════════════════════════════
// 全局状态
// ═══════════════════════════════════════════════════════════

/** @type {Map<number, Job>} 任务存储表，key为jobId */
const jobs = new Map();
/** @type {number} 任务ID自增计数器 */
let jobCounter = 0;

/**
 * 任务对象结构
 * @typedef {Object} Job
 * @property {number}   id           - 任务ID
 * @property {string}   status       - 状态: pending|running|analysis|storyboard|imaging|videogen|concat|done|error
 * @property {string}   [error]      - 错误信息
 * @property {Object}   [product]    - 产品洞察结果
 * @property {Array}    [storyboard] - 分镜脚本数组
 * @property {string[]} [images]     - 生成的图片路径
 * @property {string[]} [videoUrls]  - 各段视频URL
 * @property {string}   [finalVideo] - 最终拼接视频路径
 * @property {number}   createdAt    - 创建时间戳
 * @property {number}   [updatedAt]  - 更新时间戳
 * @property {Object}   params       - 用户传入参数
 */

// ═══════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════

/**
 * 日志输出，带时间戳前缀
 * @param {...any} args - 要输出的内容
 */
function log(...args) {
  const t = new Date().toISOString().slice(0, 19).replace('T', ' ');
  console.log(`[${t}]`, ...args);
}

/**
 * 确保输出目录存在，不存在则创建
 * @param {string} dir - 目录路径
 */
function ensureDir(dir) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
    log('创建目录:', dir);
  }
}

/**
 * 初始化系统 — 创建必要目录和作品存档文件
 */
function initSystem() {
  ensureDir(OUT_DIR);
  if (!fs.existsSync(WORKS_FILE)) {
    fs.writeFileSync(WORKS_FILE, '[]', 'utf8');
    log('初始化作品存档:', WORKS_FILE);
  }
}

/**
 * 将作品记录追加到存档
 * @param {Object} work - 作品记录对象
 */
function archiveWork(work) {
  try {
    const data = JSON.parse(fs.readFileSync(WORKS_FILE, 'utf8'));
    data.unshift({ ...work, archivedAt: Date.now() });
    // 最多保留100条
    while (data.length > 100) data.pop();
    fs.writeFileSync(WORKS_FILE, JSON.stringify(data, null, 2), 'utf8');
  } catch (e) {
    log('作品存档失败:', e.message);
  }
}

/**
 * 读取作品存档
 * @returns {Object[]} 作品记录数组
 */
function loadWorks() {
  try {
    return JSON.parse(fs.readFileSync(WORKS_FILE, 'utf8'));
  } catch {
    return [];
  }
}

/**
 * HTTP(S)请求通用封装，带超时处理
 * 出错时resolve空对象/数组，绝不reject，确保调用链不中断
 *
 * @param {string} url      - 请求URL
 * @param {Object} options  - http.request选项
 * @param {any}    [body]   - 请求体(对象或字符串)
 * @param {number} [timeoutMs] - 超时毫秒数，默认REQ_TIMEOUT_MS
 * @returns {Promise<Object>} 响应JSON或空对象
 */
function reqJSON(url, options = {}, body = null, timeoutMs = REQ_TIMEOUT_MS) {
  return new Promise((resolve) => {
    const parsed = new URL(url);
    const proto = parsed.protocol === 'https:' ? https : http;
    const postData = body ? (typeof body === 'string' ? body : JSON.stringify(body)) : null;

    const reqOpts = {
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
      path: parsed.pathname + parsed.search,
      method: options.method || 'GET',
      timeout: timeoutMs,
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        ...(options.headers || {}),
      },
    };

    const req = proto.request(reqOpts, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          resolve(data.trim() ? JSON.parse(data) : {});
        } catch {
          resolve({ raw: data });
        }
      });
    });

    req.on('error', (err) => {
      log('HTTP请求错误:', url, err.message);
      resolve({});
    });

    req.on('timeout', () => {
      log('HTTP请求超时:', url);
      req.destroy();
      resolve({});
    });

    if (postData) req.write(postData);
    req.end();
  });
}

/**
 * 下载远程文件到本地
 * @param {string} url     - 文件URL
 * @param {string} dest    - 本地保存路径
 * @param {number} [timeoutMs] - 超时，默认30秒
 * @returns {Promise<boolean>} 是否成功
 */
function downloadFile(url, dest, timeoutMs = 30000) {
  return new Promise((resolve) => {
    const proto = url.startsWith('https:') ? https : http;
    const file = fs.createWriteStream(dest);
    const req = proto.get(url, { timeout: timeoutMs }, (res) => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        // 跟随重定向
        downloadFile(res.headers.location, dest, timeoutMs).then(resolve);
        return;
      }
      res.pipe(file);
      file.on('finish', () => {
        file.close();
        resolve(true);
      });
    });
    req.on('error', () => { resolve(false); });
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

/**
 * 发送JSON响应
 * @param {http.ServerResponse} res - HTTP响应对象
 * @param {number} statusCode       - HTTP状态码
 * @param {Object} data             - 响应数据
 */
function sendJSON(res, statusCode, data) {
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
  });
  res.end(JSON.stringify(data));
}

/**
 * 发送纯文本响应
 * @param {http.ServerResponse} res - HTTP响应对象
 * @param {number} statusCode       - HTTP状态码
 * @param {string} text             - 响应文本
 */
function sendText(res, statusCode, text) {
  res.writeHead(statusCode, {
    'Content-Type': 'text/plain; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
  });
  res.end(text);
}

/**
 * 发送文件响应（支持视频流）
 * @param {http.ServerResponse} res - HTTP响应对象
 * @param {string} filePath         - 文件路径
 */
function sendFile(res, filePath) {
  if (!fs.existsSync(filePath)) {
    sendJSON(res, 404, { error: '文件不存在' });
    return;
  }
  const ext = path.extname(filePath).toLowerCase();
  const mimeTypes = {
    '.mp4': 'video/mp4', '.webm': 'video/webm', '.mov': 'video/quicktime',
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.gif': 'image/gif', '.json': 'application/json',
  };
  const stat = fs.statSync(filePath);
  res.writeHead(200, {
    'Content-Type': mimeTypes[ext] || 'application/octet-stream',
    'Content-Length': stat.size,
    'Access-Control-Allow-Origin': '*',
  });
  fs.createReadStream(filePath).pipe(res);
}

/**
 * 解析POST请求体
 * @param {http.IncomingMessage} req - HTTP请求对象
 * @returns {Promise<Object>} 解析后的JSON对象
 */
function parseBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.setEncoding('utf8');
    req.on('data', (chunk) => { data += chunk; });
    req.on('end', () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch {
        resolve({});
      }
    });
    req.on('error', () => resolve({}));
  });
}

// ═══════════════════════════════════════════════════════════
// 产品洞察模块 — 看图识产品 + DeepSeek结构化
// ═══════════════════════════════════════════════════════════

/**
 * 产品洞察分析主函数
 * 流程: hfsy网关gpt-5.5看图获取原始洞察 → DeepSeek结构化输出
 *
 * @param {string} imageUrl - 产品图片URL
 * @param {string} [productDesc] - 用户补充产品描述
 * @returns {Promise<Object>} 结构化产品洞察
 */
async function analyzeProduct(imageUrl, productDesc = '') {
  // 第一步: 调用hfsy网关gpt-5.5看图获取原始洞察
  const rawInsight = await callVisionLLM(imageUrl, productDesc);
  // 第二步: 调用DeepSeek将原始洞察结构化为标准格式
  const structured = await structureWithDeepSeek(rawInsight, productDesc);
  return structured;
}

/**
 * 调用hfsy网关GPT-5.5看图获取产品洞察
 * @param {string} imageUrl - 图片URL
 * @param {string} productDesc - 用户描述
 * @returns {Promise<string>} 原始洞察文本
 */
async function callVisionLLM(imageUrl, productDesc) {
  const body = {
    model: 'gpt-5.5',
    messages: [
      {
        role: 'user',
        content: [
          { type: 'text', text: buildVisionPrompt(productDesc) },
          { type: 'image_url', image_url: { url: imageUrl, detail: 'high' } },
        ],
      },
    ],
    max_tokens: 2048,
    temperature: 0.7,
  };

  const res = await reqJSON(ENDPOINTS.HFSY_CHAT, {
    headers: { 'Authorization': `Bearer ${CONFIG.IMAGE_API_KEY}` },
  }, body, REQ_TIMEOUT_MS);

  if (res.choices && res.choices[0] && res.choices[0].message) {
    return res.choices[0].message.content || '';
  }
  // 降级: 返回空字符串，后续DeepSeek会基于描述生成
  log('看图LLM未返回有效内容，降级处理');
  return '';
}

/**
 * 构建看图提示词
 * @param {string} productDesc - 用户描述
 * @returns {string} 提示词文本
 */
function buildVisionPrompt(productDesc) {
  return `你是一位资深电商视觉分析师。请仔细观察这张产品图片，分析并输出以下信息：
1. 产品名称和核心功能
2. 至少5个产品特性（材质、设计、功能亮点等）
3. 至少5个核心卖点（为什么消费者会购买）
4. 至少5个目标受众人群画像
5. 至少5个使用场景
${productDesc ? `\n用户补充描述: ${productDesc}` : ''}
请尽可能详细，用中文输出。`;
}

/**
 * 调用DeepSeek将原始洞察结构化为标准JSON格式
 * @param {string} rawInsight - 原始洞察文本
 * @param {string} productDesc - 用户描述
 * @returns {Promise<Object>} 结构化数据
 */
async function structureWithDeepSeek(rawInsight, productDesc) {
  const systemPrompt = `你是一位电商数据结构化专家。将提供的产品分析文本转换为严格的JSON格式，不要有任何额外输出。

输出JSON格式要求：
{
  "name": "产品名称（简短有力，不超过15字）",
  "features": ["特性1", "特性2", "特性3", "特性4", "特性5"],
  "sellingPoints": ["卖点1", "卖点2", "卖点3", "卖点4", "卖点5"],
  "audiences": ["受众1", "受众2", "受众3", "受众4", "受众5"],
  "scenarios": ["场景1", "场景2", "场景3", "场景4", "场景5"]
}

规则:
- features: 产品的物理特性和功能特点
- sellingPoints: 消费者购买的核心理由，要有说服力
- audiences: 精准人群画像，包含年龄/性别/职业/生活方式
- scenarios: 具体使用场景，描述生动
- 每项至少5条，最多8条
- 只输出JSON，不要markdown代码块标记`;

  const userPrompt = `原始分析文本:\n${rawInsight || '（无图像分析结果）'}\n\n用户描述:\n${productDesc || '（无补充描述）'}\n\n请基于以上信息输出标准JSON。`;

  const res = await reqJSON(ENDPOINTS.DEEPSEEK, {
    headers: { 'Authorization': `Bearer ${CONFIG.DEEPSEEK_API_KEY}` },
  }, {
    model: CONFIG.DEEPSEEK_MODEL,
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userPrompt },
    ],
    max_tokens: 2048,
    temperature: 0.3,
  }, REQ_TIMEOUT_MS);

  let result = { name: '', features: [], sellingPoints: [], audiences: [], scenarios: [] };

  try {
    const content = res.choices && res.choices[0] && res.choices[0].message
      ? res.choices[0].message.content
      : '';
    // 尝试从markdown代码块中提取JSON
    const jsonMatch = content.match(/```json\s*([\s\S]*?)```/) ||
                      content.match(/```\s*([\s\S]*?)```/) ||
                      content.match(/(\{[\s\S]*\})/);
    const jsonStr = jsonMatch ? jsonMatch[1] : content;
    const parsed = JSON.parse(jsonStr.trim());
    result = { ...result, ...parsed };
  } catch (e) {
    log('DeepSeek结构化解析失败，使用降级结果:', e.message);
    // 降级: 基于productDesc生成基础结构
    if (productDesc) {
      result.name = productDesc.slice(0, 15);
      result.features = [`优质${productDesc}`, '精心设计', '品质保证', '用户好评', '高性价比'];
      result.sellingPoints = ['品质卓越', '价格实惠', '设计独特', '功能强大', '售后无忧'];
      result.audiences = ['18-35岁年轻人', '都市白领', '品质生活追求者', '新手用户', '送礼人群'];
      result.scenarios = ['日常使用', '办公场景', '居家放松', '外出携带', '节日送礼'];
    }
  }

  return result;
}

// ═══════════════════════════════════════════════════════════
// 分镜脚本模块 — DeepSeek生成9字段故事板
// ═══════════════════════════════════════════════════════════

/**
 * 生成分镜脚本
 * 按照 钩子→卖点→场景→促单 的递进结构组织N段分镜
 * 每段包含完整的9字段故事板信息
 *
 * @param {Object} product   - 产品洞察结果
 * @param {number} [numSegments] - 分镜段数，默认DEFAULT_SEGMENTS
 * @returns {Promise<Array>} 分镜数组
 */
async function generateStoryboard(product, numSegments = DEFAULT_SEGMENTS) {
  const systemPrompt = `你是一位顶级电商短视频导演。请将产品信息转化为专业的带货视频分镜脚本。

分镜结构要求（严格按此递进）：
第1段 — 钩子(Hook): 前3秒抓眼球，制造悬念或冲击
第2段 — 卖点(Selling): 展示核心卖点，产品优势
第3段 — 场景(Scene): 使用场景展示，引发代入感
第4段 — 促单(CTA): 限时优惠、行动号召、下单引导

每段必须包含以下9个字段的JSON对象：
{
  "镜号": number,
  "景别": "特写|近景|中景|全景|远景",
  "运镜": "固定|推|拉|摇|移|跟|升|降|环绕",
  "时长s": number,
  "画面": "详细的画面描述（光线、色彩、构图、主体位置）",
  "台词": "口播文案（口语化、有感染力、带emoji风格）",
  "素材": "产品图/人物图/场景图/文字卡",
  "锚定": "这一帧在整条视频中的作用（为什么放这里）",
  "转场": "切|淡入淡出|划像|缩放|翻页|无"
}

规则:
- 总段数严格按用户指定
- 每段时长3-8秒，总时长控制在15-30秒
- 台词要有感染力，适合口播
- 画面描述要详细到可让AI生图
- 只输出JSON数组，不要markdown标记`;

  const userPrompt = `产品信息:
名称: ${product.name || '未知产品'}
特性: ${(product.features || []).join('、')}
卖点: ${(product.sellingPoints || []).join('、')}
受众: ${(product.audiences || []).join('、')}
场景: ${(product.scenarios || []).join('、')}

请生成${numSegments}段分镜脚本，输出JSON数组。`;

  const res = await reqJSON(ENDPOINTS.DEEPSEEK, {
    headers: { 'Authorization': `Bearer ${CONFIG.DEEPSEEK_API_KEY}` },
  }, {
    model: CONFIG.DEEPSEEK_MODEL,
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userPrompt },
    ],
    max_tokens: 4096,
    temperature: 0.8,
  }, REQ_TIMEOUT_MS);

  let storyboard = [];

  try {
    const content = res.choices && res.choices[0] && res.choices[0].message
      ? res.choices[0].message.content
      : '';
    const jsonMatch = content.match(/```json\s*([\s\S]*?)```/) ||
                      content.match(/```\s*([\s\S]*?)```/) ||
                      content.match(/(\[[\s\S]*\])/);
    const jsonStr = jsonMatch ? jsonMatch[1] : content;
    storyboard = JSON.parse(jsonStr.trim());
  } catch (e) {
    log('分镜脚本解析失败，使用降级模板:', e.message);
    storyboard = getFallbackStoryboard(product, numSegments);
  }

  // 确保每段都有9个字段，缺失的补默认值
  return storyboard.map((seg, idx) => ({
    '镜号': seg['镜号'] || idx + 1,
    '景别': seg['景别'] || '中景',
    '运镜': seg['运镜'] || '固定',
    '时长s': seg['时长s'] || 5,
    '画面': seg['画面'] || `${product.name || '产品'}展示画面`,
    '台词': seg['台词'] || '快来看看这款超棒的产品！',
    '素材': seg['素材'] || '产品图',
    '锚定': seg['锚定'] || `第${idx + 1}段分镜`,
    '转场': seg['转场'] || '切',
  }));
}

/**
 * 分镜降级模板 — 当DeepSeek调用失败时使用
 * @param {Object} product - 产品信息
 * @param {number} numSegments - 段数
 * @returns {Array} 基础分镜数组
 */
function getFallbackStoryboard(product, numSegments) {
  const name = product.name || '这款产品';
  const templates = [
    {
      '镜号': 1, '景别': '特写', '运镜': '推', '时长s': 3,
      '画面': `特写镜头，${name}最亮眼的细节部分在柔和光线下缓缓呈现，背景虚化`,
      '台词': `OMG！你绝对没见过的${name}，今天终于来了！`,
      '素材': '产品图', '锚定': '3秒钩子抓眼球', '转场': '缩放',
    },
    {
      '镜号': 2, '景别': '中景', '运镜': '环绕', '时长s': 5,
      '画面': `360度环绕展示${name}，多角度呈现产品外观，明亮干净的背景`,
      '台词': `${(product.sellingPoints || ['超好用'])[0]}，${(product.sellingPoints || ['设计精美'])[1] || '设计精美'}，每一个细节都经得起考验！`,
      '素材': '产品图', '锚定': '核心卖点展示', '转场': '切',
    },
    {
      '镜号': 3, '景别': '全景', '运镜': '移', '时长s': 5,
      '画面': `温馨的真实使用场景，模特手持${name}自然使用，阳光明媚的室内环境`,
      '台词': `无论是${(product.scenarios || ['日常使用'])[0]}还是${(product.scenarios || [''][1]) || '出门旅行'}，有它在身边就是安心！`,
      '素材': '场景图', '锚定': '场景代入感', '转场': '淡入淡出',
    },
    {
      '镜号': 4, '景别': '近景', '运镜': '固定', '时长s': 4,
      '画面': `${name}特写+醒目的价格标签和购买按钮动画叠加，背景暖色调`,
      '台词': `现在下单还有专属优惠！点击链接，把${name}带回家！`,
      '素材': '文字卡', '锚定': '促单转化', '转场': '无',
    },
  ];
  return templates.slice(0, numSegments);
}

// ═══════════════════════════════════════════════════════════
// 图片生成模块 — hfsy网关 + 多模型降级
// ═══════════════════════════════════════════════════════════

/**
 * 图片生成主函数
 * 支持图生图参考（公网URL），失败时自动降级为文生图
 *
 * @param {string} prompt       - 图片生成提示词
 * @param {string} outputPath   - 本地保存路径
 * @param {string} [refImageUrl] - 参考图URL(图生图)
 * @returns {Promise<boolean>} 是否成功
 */
async function genImage(prompt, outputPath, refImageUrl = '') {
  // 首先尝试hfsy网关gpt-image-2
  let ok = await genImageHfsy(prompt, outputPath, refImageUrl);
  if (ok) return true;

  // 降级: 不带参考图重试
  if (refImageUrl) {
    log('图生图失败，降级为纯文生图:', prompt.slice(0, 50));
    ok = await genImageHfsy(prompt, outputPath, '');
    if (ok) return true;
  }

  log('图片生成全部失败:', prompt.slice(0, 50));
  return false;
}

/**
 * 调用hfsy网关gpt-image-2生成图片
 * @param {string} prompt      - 提示词
 * @param {string} outputPath  - 保存路径
 * @param {string} refImageUrl - 参考图URL
 * @returns {Promise<boolean>} 是否成功
 */
async function genImageHfsy(prompt, outputPath, refImageUrl) {
  const body = {
    model: 'gpt-image-2',
    prompt: prompt,
    n: 1,
    size: '1024x1792',  // 竖屏比例 9:16
    quality: 'high',
    ...(refImageUrl ? { image: refImageUrl } : {}),
  };

  const res = await reqJSON(ENDPOINTS.HFSY_IMAGE, {
    headers: { 'Authorization': `Bearer ${CONFIG.IMAGE_API_KEY}` },
  }, body, IMAGE_TIMEOUT_MS);

  if (res.data && res.data[0] && res.data[0].url) {
    const ok = await downloadFile(res.data[0].url, outputPath);
    if (ok) {
      log('图片生成成功:', outputPath);
      return true;
    }
  }
  return false;
}

/**
 * 批量生成分镜关键帧图片
 * @param {Array} storyboard   - 分镜数组
 * @param {Object} product     - 产品信息
 * @param {string} jobDir      - 任务目录
 * @returns {Promise<string[]>} 生成的图片路径数组
 */
async function generateStoryboardImages(storyboard, product, jobDir) {
  const images = [];
  const imgDir = path.join(jobDir, 'images');
  ensureDir(imgDir);

  for (let i = 0; i < storyboard.length; i++) {
    const seg = storyboard[i];
    // 基于画面描述增强提示词
    const enhancedPrompt = enhanceImagePrompt(seg['画面'], product);
    const imgPath = path.join(imgDir, `frame_${String(i + 1).padStart(2, '0')}.png`);

    const ok = await genImage(enhancedPrompt, imgPath);
    if (ok) {
      images.push(imgPath);
    } else {
      // 如果生成失败，使用占位图标记，后续视频生成跳过此段
      images.push('');
    }
  }

  return images;
}

/**
 * 增强图片生成提示词
 * @param {string} sceneDesc - 画面描述
 * @param {Object} product   - 产品信息
 * @returns {string} 增强后的英文提示词
 */
function enhanceImagePrompt(sceneDesc, product) {
  const base = sceneDesc || `${product.name || 'product'} showcase`;
  // 将中文画面描述翻译/增强为英文生图提示词
  return `Professional product photography, ${base}, ${product.name || 'product'} as the main subject, cinematic lighting, high quality, 8k, sharp focus, clean background, commercial advertisement style, vertical composition 9:16`;
}



// ═══════════════════════════════════════════════════════════
// 视频生成模块 — 丽帧API + Agnes回退
// ═══════════════════════════════════════════════════════════

/**
 * 生成单段视频
 * 流程: 丽帧主API → 失败则 Agnes回退
 *
 * @param {string} prompt     - 视频生成提示词
 * @param {string} imagePath  - 首帧图片路径(本地)
 * @param {string} outputPath - 输出视频路径
 * @param {number} [duration] - 视频时长(秒)，默认5
 * @returns {Promise<boolean>} 是否成功
 */
async function genSeg(prompt, imagePath, outputPath, duration = 5) {
  // 优先使用丽帧
  if (CONFIG.VIDEO_BACKEND === 'kuaizi' && CONFIG.KUAIZI_API_KEY) {
    const ok = await genSegKuaizi(prompt, imagePath, outputPath, duration);
    if (ok) return true;
    log('丽帧生成失败，尝试Agnes回退...');
  }
  // 回退到Agnes
  if (CONFIG.AGNES_API_KEY) {
    return await genSegAgnes(prompt, imagePath, outputPath, duration);
  }
  log('无可用视频生成后端');
  return false;
}

/**
 * 丽帧API生成视频
 * 流程: 创建任务 → 轮询状态 → 下载结果
 *
 * @param {string} prompt     - 视频提示词
 * @param {string} imagePath  - 首帧图片路径
 * @param {string} outputPath - 输出路径
 * @param {number} duration   - 时长(秒)
 * @returns {Promise<boolean>} 是否成功
 */
async function genSegKuaizi(prompt, imagePath, outputPath, duration) {
  // 如果首帧图存在，上传获取公网URL(丽帧需要公网URL)
  let imageUrl = '';
  if (imagePath && fs.existsSync(imagePath)) {
    // 将图片转为base64 data URL
    const base64 = fs.readFileSync(imagePath).toString('base64');
    imageUrl = `data:image/png;base64,${base64}`;
  }

  // 1. 创建任务
  const createBody = {
    prompt: prompt,
    mode: 'fast',
    resolution: '720p',
    ratio: '9:16',
    duration: duration,
    generate_audio: true,
    images: imageUrl ? [{ url: imageUrl, role: 'first_frame' }] : [],
  };

  log('丽帧创建任务:', prompt.slice(0, 60));
  const createRes = await reqJSON(ENDPOINTS.KUAIZI_CREATE, {
    headers: {
      'ApiKey': CONFIG.KUAIZI_API_KEY,
      'Content-Type': 'application/json',
    },
  }, createBody, REQ_TIMEOUT_MS);

  if (!createRes.data || !createRes.data.task_id) {
    log('丽帧创建任务失败:', JSON.stringify(createRes));
    return false;
  }

  const taskId = createRes.data.task_id;
  log('丽帧任务创建成功，taskId:', taskId);

  // 2. 轮询任务状态
  const videoUrl = await pollKuaiziTask(taskId);
  if (!videoUrl) {
    log('丽帧任务轮询失败');
    return false;
  }

  // 3. 下载视频文件
  const ok = await downloadFile(videoUrl, outputPath, 60000);
  if (ok) {
    log('丽帧视频下载成功:', outputPath);
  }
  return ok;
}

/**
 * 轮询丽帧视频生成任务状态
 * @param {string} taskId - 丽帧任务ID
 * @returns {Promise<string|null>} 视频URL或null
 */
async function pollKuaiziTask(taskId) {
  for (let i = 0; i < MAX_POLL_COUNT; i++) {
    await sleep(POLL_INTERVAL_MS);

    const res = await reqJSON(`${ENDPOINTS.KUAIZI_STATUS}?task_id=${taskId}`, {
      headers: { 'ApiKey': CONFIG.KUAIZI_API_KEY },
    }, null, 10000);

    const status = res.data && res.data.status ? res.data.status : '';
    log(`丽帧任务[${taskId}] 状态: ${status} (${i + 1}/${MAX_POLL_COUNT})`);

    if (status === 'SUCCESS' && res.data.video_url) {
      return res.data.video_url;
    }
    if (status === 'FAILED') {
      log('丽帧任务执行失败:', res.data && res.data.error_msg);
      return null;
    }
    // 继续轮询 (PROCESSING / PENDING)
  }

  log('丽帧任务轮询超时');
  return null;
}

/**
 * Agnes API生成视频(回退方案)
 * @param {string} prompt     - 视频提示词
 * @param {string} imagePath  - 首帧图片路径
 * @param {string} outputPath - 输出路径
 * @param {number} duration   - 时长(秒)
 * @returns {Promise<boolean>} 是否成功
 */
async function genSegAgnes(prompt, imagePath, outputPath, duration) {
  const body = {
    model: 'agnes-video-v1',
    prompt: prompt,
    duration: duration,
    resolution: '720p',
    ratio: '9:16',
  };

  // 如果有参考图，转为base64
  if (imagePath && fs.existsSync(imagePath)) {
    body.image = fs.readFileSync(imagePath).toString('base64');
  }

  log('Agnes创建任务:', prompt.slice(0, 60));
  const res = await reqJSON(ENDPOINTS.AGNES, {
    headers: { 'Authorization': `Bearer ${CONFIG.AGNES_API_KEY}` },
  }, body, REQ_TIMEOUT_MS);

  if (res.data && res.data.video_url) {
    const ok = await downloadFile(res.data.video_url, outputPath, 60000);
    if (ok) log('Agnes视频下载成功:', outputPath);
    return ok;
  }

  // Agnes也支持轮询模式
  if (res.data && res.data.task_id) {
    const videoUrl = await pollAgnesTask(res.data.task_id);
    if (videoUrl) {
      return await downloadFile(videoUrl, outputPath, 60000);
    }
  }

  log('Agnes生成视频失败:', JSON.stringify(res));
  return false;
}

/**
 * 轮询Agnes视频任务
 * @param {string} taskId - Agnes任务ID
 * @returns {Promise<string|null>} 视频URL或null
 */
async function pollAgnesTask(taskId) {
  for (let i = 0; i < MAX_POLL_COUNT; i++) {
    await sleep(POLL_INTERVAL_MS);

    const res = await reqJSON(`${ENDPOINTS.AGNES}/${taskId}`, {
      headers: { 'Authorization': `Bearer ${CONFIG.AGNES_API_KEY}` },
    }, null, 10000);

    const status = res.data && res.data.status ? res.data.status : '';
    log(`Agnes任务[${taskId}] 状态: ${status} (${i + 1}/${MAX_POLL_COUNT})`);

    if (status === 'completed' && res.data.video_url) {
      return res.data.video_url;
    }
    if (status === 'failed') {
      log('Agnes任务执行失败');
      return null;
    }
  }
  log('Agnes任务轮询超时');
  return null;
}

/**
 * 批量生成各分镜段视频
 * @param {Array} storyboard  - 分镜数组
 * @param {string[]} images   - 图片路径数组
 * @param {string} jobDir     - 任务目录
 * @returns {Promise<string[]>} 成功生成的视频路径数组
 */
async function generateSegmentVideos(storyboard, images, jobDir) {
  const videoDir = path.join(jobDir, 'segments');
  ensureDir(videoDir);
  const videos = [];

  for (let i = 0; i < storyboard.length; i++) {
    const seg = storyboard[i];
    const segPath = path.join(videoDir, `seg_${String(i + 1).padStart(2, '0')}.mp4`);

    // 基于分镜信息构建视频生成提示词
    const videoPrompt = buildVideoPrompt(seg);
    const imgPath = images[i] || '';
    const duration = seg['时长s'] || 5;

    const ok = await genSeg(videoPrompt, imgPath, segPath, duration);
    if (ok) {
      videos.push(segPath);
    } else {
      log(`第${i + 1}段视频生成失败，跳过`);
    }
  }

  return videos;
}

/**
 * 构建视频生成提示词
 * @param {Object} seg - 单段分镜
 * @returns {string} 英文视频提示词
 */
function buildVideoPrompt(seg) {
  const scene = seg['画面'] || 'product showcase';
  const camera = seg['运镜'] || 'static';
  const shot = seg['景别'] || 'medium shot';

  const cameraMap = {
    '推': 'push in', '拉': 'pull out', '摇': 'pan', '移': 'truck',
    '跟': 'tracking', '升': 'crane up', '降': 'crane down',
    '环绕': 'orbit', '固定': 'static',
  };

  const shotMap = {
    '特写': 'close-up', '近景': 'medium close-up', '中景': 'medium shot',
    '全景': 'wide shot', '远景': 'long shot',
  };

  const cameraEn = cameraMap[camera] || camera;
  const shotEn = shotMap[shot] || shot;

  return `${scene}, ${shotEn}, ${cameraEn} camera movement, professional commercial video, smooth motion, high quality, cinematic lighting, 9:16 vertical format`;
}

// ═══════════════════════════════════════════════════════════
// ffmpeg拼接模块 — 多段concat + scale/crop
// ═══════════════════════════════════════════════════════════

/**
 * 拼接多段视频为最终成片
 * 使用ffmpeg concat协议 + 统一scale/crop到720x1280竖屏
 *
 * @param {string[]} videoFiles - 视频文件路径数组
 * @param {string} outputFile   - 最终输出路径
 * @returns {Promise<boolean>} 是否成功
 */
async function finalizeVideo(videoFiles, outputFile) {
  if (!videoFiles || videoFiles.length === 0) {
    log('没有视频段可拼接');
    return false;
  }

  // 检查ffmpeg是否可用
  if (!checkFfmpeg()) {
    log('ffmpeg未安装，尝试纯拷贝模式');
    return fallbackConcat(videoFiles, outputFile);
  }

  try {
    // 步骤1: 预处理每段视频 — 统一分辨率、编码格式、帧率
    const normalizedFiles = [];
    const tempDir = path.join(path.dirname(outputFile), '_temp');
    ensureDir(tempDir);

    for (let i = 0; i < videoFiles.length; i++) {
      const normalized = path.join(tempDir, `norm_${String(i).padStart(3, '0')}.mp4`);
      const cmd = `ffmpeg -y -i "${videoFiles[i]}" -vf "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black" -r 30 -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k -ar 44100 -movflags +faststart "${normalized}" 2>/dev/null`;
      execSync(cmd, { timeout: 60000 });
      normalizedFiles.push(normalized);
    }

    // 步骤2: 生成concat列表文件
    const listFile = path.join(tempDir, 'concat_list.txt');
    const listContent = normalizedFiles.map(f => `file '${f}'`).join('\n');
    fs.writeFileSync(listFile, listContent, 'utf8');

    // 步骤3: ffmpeg concat拼接
    const concatCmd = `ffmpeg -y -f concat -safe 0 -i "${listFile}" -c copy -movflags +faststart "${outputFile}" 2>/dev/null`;
    execSync(concatCmd, { timeout: 120000 });

    // 步骤4: 清理临时文件
    try {
      fs.rmSync(tempDir, { recursive: true, force: true });
    } catch {
      // 忽略清理错误
    }

    if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 1024) {
      log('视频拼接成功:', outputFile);
      return true;
    }
  } catch (e) {
    log('ffmpeg拼接失败:', e.message);
    return fallbackConcat(videoFiles, outputFile);
  }

  return false;
}

/**
 * 降级拼接方案 — 当ffmpeg不可用时，直接拷贝第一段
 * @param {string[]} videoFiles - 视频路径数组
 * @param {string} outputFile   - 输出路径
 * @returns {boolean} 是否成功
 */
function fallbackConcat(videoFiles, outputFile) {
  try {
    // 降级: 只使用第一段视频
    if (videoFiles[0] && fs.existsSync(videoFiles[0])) {
      fs.copyFileSync(videoFiles[0], outputFile);
      log('降级拷贝视频:', outputFile);
      return true;
    }
  } catch (e) {
    log('降级拷贝也失败:', e.message);
  }
  return false;
}

/**
 * 检查ffmpeg是否已安装
 * @returns {boolean} 是否可用
 */
function checkFfmpeg() {
  try {
    execSync('ffmpeg -version', { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

// ═══════════════════════════════════════════════════════════
// 任务调度 — 异步后台执行引擎
// ═══════════════════════════════════════════════════════════

/**
 * 创建新任务并启动异步后台执行
 * 立即返回jobId，客户端通过轮询查询进度
 *
 * @param {Object} params - 用户传入的生成参数
 * @returns {number} 任务ID
 */
function createJob(params) {
  const jobId = ++jobCounter;
  const job = {
    id: jobId,
    status: 'pending',
    error: null,
    product: null,
    storyboard: null,
    images: [],
    videoUrls: [],
    finalVideo: null,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    params: params,
  };
  jobs.set(jobId, job);

  // 启动异步执行
  runJobAsync(jobId);

  return jobId;
}

/**
 * 异步执行任务主流程
 * 流程: pending → analysis → storyboard → imaging → videogen → concat → done
 *
 * @param {number} jobId - 任务ID
 */
async function runJobAsync(jobId) {
  const job = jobs.get(jobId);
  if (!job) return;

  const jobDir = path.join(OUT_DIR, `job_${jobId}`);
  ensureDir(jobDir);

  try {
    // === 阶段1: 产品洞察 ===
    job.status = 'analysis';
    job.updatedAt = Date.now();
    log(`[Job ${jobId}] 开始产品洞察...`);

    const productImage = job.params.imageUrl || '';
    const productDesc = job.params.productDesc || job.params.description || '';
    job.product = await analyzeProduct(productImage, productDesc);
    log(`[Job ${jobId}] 产品洞察完成:`, job.product.name);

    // === 阶段2: 分镜脚本 ===
    job.status = 'storyboard';
    job.updatedAt = Date.now();
    log(`[Job ${jobId}] 开始生成分镜脚本...`);

    const numSegments = job.params.segments || DEFAULT_SEGMENTS;
    job.storyboard = await generateStoryboard(job.product, numSegments);
    log(`[Job ${jobId}] 分镜脚本完成:`, job.storyboard.length, '段');

    // === 阶段3: 图片生成 ===
    job.status = 'imaging';
    job.updatedAt = Date.now();
    log(`[Job ${jobId}] 开始生成分镜关键帧...`);

    job.images = await generateStoryboardImages(job.storyboard, job.product, jobDir);
    log(`[Job ${jobId}] 图片生成完成:`, job.images.filter(Boolean).length, '张');

    // === 阶段4: 视频生成 ===
    job.status = 'videogen';
    job.updatedAt = Date.now();
    log(`[Job ${jobId}] 开始生成视频片段...`);

    const segmentVideos = await generateSegmentVideos(
      job.storyboard, job.images, jobDir
    );
    log(`[Job ${jobId}] 视频生成完成:`, segmentVideos.length, '段');

    // === 阶段5: ffmpeg拼接 ===
    job.status = 'concat';
    job.updatedAt = Date.now();
    log(`[Job ${jobId}] 开始拼接最终视频...`);

    const finalVideoPath = path.join(jobDir, 'final.mp4');
    const concatOk = await finalizeVideo(segmentVideos, finalVideoPath);

    if (concatOk) {
      job.finalVideo = finalVideoPath;
      job.status = 'done';
      log(`[Job ${jobId}] 视频生成全部完成!`);

      // 存档作品
      archiveWork({
        jobId: jobId,
        productName: job.product.name,
        segments: job.storyboard.length,
        videoPath: finalVideoPath,
        createdAt: job.createdAt,
      });
    } else {
      throw new Error('视频拼接失败');
    }
  } catch (e) {
    job.status = 'error';
    job.error = e.message || '未知错误';
    log(`[Job ${jobId}] 任务执行失败:`, e.message);
  }

  job.updatedAt = Date.now();
}

/**
 * 获取任务状态（供API轮询使用）
 * @param {number} jobId - 任务ID
 * @returns {Object|null} 任务状态对象
 */
function getJobStatus(jobId) {
  const job = jobs.get(jobId);
  if (!job) return null;

  const base = {
    id: job.id,
    status: job.status,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
  };

  // 根据状态返回不同详细程度的信息
  if (job.status === 'error') {
    return { ...base, error: job.error };
  }

  if (job.status === 'done') {
    return {
      ...base,
      product: job.product,
      storyboard: job.storyboard,
      finalVideo: job.finalVideo,
      videoUrl: job.finalVideo ? `/out/job_${jobId}/final.mp4` : null,
    };
  }

  // 中间状态: 返回当前阶段已有数据
  return {
    ...base,
    product: job.product,
    storyboard: job.storyboard,
    images: job.images,
  };
}

// ═══════════════════════════════════════════════════════════
// HTTP服务层 — API路由 + 静态文件服务
// ═══════════════════════════════════════════════════════════

/**
 * HTTP请求路由分发
 * @param {http.IncomingMessage} req - 请求对象
 * @param {http.ServerResponse} res  - 响应对象
 */
async function handleRequest(req, res) {
  const url = req.url || '/';
  const method = req.method || 'GET';

  // 全局CORS预检
  if (method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    });
    res.end();
    return;
  }

  // API路由分发
  try {
    // GET  /api/config   — 服务状态与配置
    if (url === '/api/config' && method === 'GET') {
      return handleConfig(req, res);
    }

    // POST /api/generate — 创建生成任务
    if (url === '/api/generate' && method === 'POST') {
      return handleGenerate(req, res);
    }

    // GET  /api/jobs/:id — 查询任务状态
    const jobMatch = url.match(/^\/api\/jobs\/(\d+)$/);
    if (jobMatch && method === 'GET') {
      return handleJobStatus(req, res, parseInt(jobMatch[1], 10));
    }

    // GET  /api/works    — 历史作品列表
    if (url === '/api/works' && method === 'GET') {
      return handleWorks(req, res);
    }

    // GET  /out/:file    — 输出文件服务
    const outMatch = url.match(/^\/out\/(.*)$/);
    if (outMatch && method === 'GET') {
      return handleFileServe(req, res, outMatch[1]);
    }

    // 404 未匹配路由
    sendJSON(res, 404, { error: '接口不存在', path: url, method });
  } catch (e) {
    log('请求处理异常:', e.message);
    sendJSON(res, 500, { error: '服务器内部错误', detail: e.message });
  }
}

/**
 * GET /api/config — 获取服务状态与配置信息
 * 返回: { status, version, backends, uptime }
 */
function handleConfig(req, res) {
  const backends = {
    deepseek: !!CONFIG.DEEPSEEK_API_KEY,
    hfsy:     !!CONFIG.IMAGE_API_KEY,
    kuaizi:   !!CONFIG.KUAIZI_API_KEY,
    agnes:    !!CONFIG.AGNES_API_KEY,
    kimi:     !!CONFIG.KIMI_API_KEY,
    ffmpeg:   checkFfmpeg(),
  };

  sendJSON(res, 200, {
    status: 'ok',
    version: '2.0.0',
    backends,
    uptime: process.uptime(),
    timestamp: Date.now(),
    config: {
      port: PORT,
      outDir: OUT_DIR,
      videoBackend: CONFIG.VIDEO_BACKEND,
      deepseekModel: CONFIG.DEEPSEEK_MODEL,
    },
  });
}

/**
 * POST /api/generate — 创建视频生成任务
 * 请求体: { imageUrl, productDesc?, description?, segments?, refImageUrl? }
 * 返回:   { jobId, status, message }
 */
async function handleGenerate(req, res) {
  const body = await parseBody(req);

  // 参数校验
  if (!body.imageUrl) {
    return sendJSON(res, 400, { error: '缺少必填参数: imageUrl' });
  }

  // 创建任务
  const jobId = createJob({
    imageUrl: body.imageUrl,
    productDesc: body.productDesc || body.description || '',
    segments: Math.min(Math.max(parseInt(body.segments, 10) || DEFAULT_SEGMENTS, 1), 8),
    refImageUrl: body.refImageUrl || '',
    style: body.style || '',
    music: body.music || '',
  });

  log('创建任务:', jobId, 'imageUrl:', body.imageUrl.slice(0, 60));

  sendJSON(res, 202, {
    jobId,
    status: 'pending',
    message: '任务已创建，请通过 /api/jobs/' + jobId + ' 轮询状态',
  });
}

/**
 * GET /api/jobs/:id — 查询任务状态
 * 返回: 当前阶段所有可用数据 + 状态
 */
function handleJobStatus(req, res, jobId) {
  const status = getJobStatus(jobId);
  if (!status) {
    return sendJSON(res, 404, { error: '任务不存在', jobId });
  }
  sendJSON(res, 200, status);
}

/**
 * GET /api/works — 历史作品列表
 * 返回: { works: Array }
 */
function handleWorks(req, res) {
  const works = loadWorks();
  sendJSON(res, 200, {
    works: works.map(w => ({
      jobId: w.jobId,
      productName: w.productName,
      segments: w.segments,
      videoUrl: w.videoPath ? `/out/${path.relative(OUT_DIR, w.videoPath)}` : null,
      createdAt: w.createdAt,
      archivedAt: w.archivedAt,
    })),
  });
}

/**
 * GET /out/:file — 输出文件静态服务
 * 支持视频文件流式传输
 */
function handleFileServe(req, res, filePath) {
  // 安全检查: 防止目录穿越
  const safePath = path.normalize(filePath).replace(/^(\.\.[\/\\])+/, '');
  const fullPath = path.join(OUT_DIR, safePath);

  // 确保在输出目录内
  if (!fullPath.startsWith(OUT_DIR)) {
    return sendJSON(res, 403, { error: '禁止访问' });
  }

  sendFile(res, fullPath);
}

// ═══════════════════════════════════════════════════════════
// 服务器启动入口
// ═══════════════════════════════════════════════════════════

/**
 * 启动HTTP服务器
 */
function startServer() {
  initSystem();

  const server = http.createServer((req, res) => {
    handleRequest(req, res);
  });

  server.listen(PORT, () => {
    log('╔═══════════════════════════════════════════╗');
    log('║   VIDEO-FACTORY 核心引擎 v2.0.0           ║');
    log('║   AI带货视频生成系统                       ║');
    log('╚═══════════════════════════════════════════╝');
    log(`服务已启动: http://0.0.0.0:${PORT}`);
    log('输出目录:', OUT_DIR);
    log('可用后端:');
    log('  DeepSeek :', CONFIG.DEEPSEEK_API_KEY ? '已配置' : '未配置');
    log('  hfsy     :', CONFIG.IMAGE_API_KEY ? '已配置' : '未配置');
    log('  丽帧     :', CONFIG.KUAIZI_API_KEY ? '已配置' : '未配置');
    log('  Agnes    :', CONFIG.AGNES_API_KEY ? '已配置' : '未配置');
    log('  ffmpeg   :', checkFfmpeg() ? '已安装' : '未安装');
  });

  // 优雅退出
  process.on('SIGINT', () => {
    log('\n收到SIGINT信号，正在关闭服务...');
    server.close(() => {
      log('服务已关闭');
      process.exit(0);
    });
  });

  process.on('SIGTERM', () => {
    log('\n收到SIGTERM信号，正在关闭服务...');
    server.close(() => {
      log('服务已关闭');
      process.exit(0);
    });
  });

  // 未捕获异常处理，防止服务崩溃
  process.on('uncaughtException', (err) => {
    log('未捕获异常:', err.message);
  });

  process.on('unhandledRejection', (reason) => {
    log('未处理的Promise拒绝:', reason);
  });
}

/**
 * 睡眠辅助函数
 * @param {number} ms - 毫秒数
 * @returns {Promise<void>}
 */
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// 启动
startServer();

// 导出（供测试使用）
module.exports = {
  startServer,
  createJob,
  getJobStatus,
  analyzeProduct,
  generateStoryboard,
  genImage,
  genSeg,
  finalizeVideo,
  CONFIG,
};
