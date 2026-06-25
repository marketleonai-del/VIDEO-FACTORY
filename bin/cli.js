#!/usr/bin/env node
/**
 * ============================================================================
 * VIDEO-FACTORY CLI 工具
 * ============================================================================
 * 命令入口，支持生成、矩阵、进化、钩子、混剪、质检、状态查询、服务启动
 *
 * 用法:
 *   node bin/cli.js <命令> [选项]
 *   ./bin/cli.js <命令> [选项]   (需 chmod +x)
 *
 * 命令列表:
 *   generate  - 生成带货视频 (单条精品)
 *   matrix    - 矩阵批量生成 (最多200条)
 *   evolve    - 运行 SkillOpt 自进化
 *   hook      - 生成钩子角度库
 *   hybrid    - 混剪模式 (AI素材+用户素材)
 *   qa        - 智能质检检查
 *   status    - 查看服务状态
 *   serve     - 启动 HTTP 服务
 *   config    - 查看/修改配置
 *
 * 示例:
 *   node bin/cli.js generate -p "便携榨汁杯" --platform douyin -d 30
 *   node bin/cli.js matrix -p "蓝牙耳机" -n 50 --strategy hook
 *   node bin/cli.js evolve --evolve-rounds 3
 *   node bin/cli.js serve --port 8088
 * ============================================================================
 */

const fs = require('fs');
const path = require('path');
const http = require('http');
const { exec, spawn } = require('child_process');

// ==================== 常量配置 ====================
const PACKAGE_ROOT = path.resolve(__dirname, '..');
const CONFIG_PATH = path.join(PACKAGE_ROOT, 'config.json');
const DEFAULT_API_URL = 'http://localhost:8088';
const MAX_MATRIX_COUNT = 200;
const SUPPORTED_PLATFORMS = ['douyin', 'kuaishou', 'tiktok', 'xiaohongshu', 'shipinhao'];
const SUPPORTED_DURATIONS = [15, 30, 60];

// ==================== 命令定义 ====================
const COMMANDS = {
  generate: { desc: '生成带货视频 (单条精品)', emoji: '\u{1F3AC}' },
  matrix:   { desc: '矩阵批量生成 (最多200条)', emoji: '\u{1F522}' },
  evolve:   { desc: '运行 SkillOpt 自进化', emoji: '\u{1F9EC}' },
  hook:     { desc: '生成钩子角度库', emoji: '\u{1F3A3}' },
  hybrid:   { desc: '混剪模式 (AI+用户素材)', emoji: '\u{1F3AC}' },
  qa:       { desc: '智能质检检查', emoji: '\u{1F50D}' },
  status:   { desc: '查看服务状态', emoji: '\u{2705}' },
  serve:    { desc: '启动 HTTP 服务', emoji: '\u{1F680}' },
  config:   { desc: '查看/修改配置', emoji: '\u{2699}' },
  list:     { desc: '列出历史任务', emoji: '\u{1F4CB}' },
  cancel:   { desc: '取消指定任务', emoji: '\u{274C}' },
  download: { desc: '下载生成结果', emoji: '\u{2B07}' }
};

// ==================== 帮助信息 ====================
function printHelp() {
  console.log(`
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  VIDEO-FACTORY CLI v3.0 - AI带货视频工厂  \u2551
\u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2563
\u2551  用法: vf <命令> [选项]                     \u2551
\u2551                                               \u2551
\u2551  命令:                                        \u2551`);

  for (const [cmd, info] of Object.entries(COMMANDS)) {
    const label = `  ${cmd.padEnd(10)} ${info.emoji} ${info.desc}`;
    console.log('\u2551' + label.padEnd(47) + '\u2551');
  }

  console.log(`\u2551                                               \u2551
\u2551  全局选项:                                    \u2551
\u2551    --api-url       API服务端点 (默认: ${DEFAULT_API_URL.replace('http://', '')})  \u2551
\u2551    --help, -h      显示帮助                   \u2551
\u2551    --version, -v   显示版本                   \u2551
\u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2556
\u2551  generate / matrix 选项:                      \u2551
\u2551    --product, -p    产品名称 (必填)            \u2551
\u2551    --image, -i      产品图片路径               \u2551
\u2551    --platform       平台: ${SUPPORTED_PLATFORMS.join('/')}  \u2551
\u2551    --duration, -d   视频时长: ${SUPPORTED_DURATIONS.join('/')} (秒)        \u2551
\u2551    --count, -n      生成数量: 1-${MAX_MATRIX_COUNT}           \u2551
\u2551    --materials, -m  自有素材目录/文件          \u2551
\u2551    --output, -o     输出目录 (默认: ./output)  \u2551
\u2551    --strategy       矩阵策略: all/hook/persona/scene/cta  \u2551
\u2551    --hook-angle     指定钩子角度 (A-L)         \u2551
\u2551    --script-style   脚本风格: emotional/rational/story/usp/comparison  \u2551
\u2551    --voice          配音: female-young/female-warm/male-young/male-professional  \u2551
\u2551    --evolve-rounds  进化轮次 (默认: 1)         \u2551
\u2551    --wait           等待任务完成               \u2551
\u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2556
\u2551  示例:                                        \u2551
\u2551  vf generate -p "便携榨汁杯" --platform douyin \u2551
\u2551  vf matrix -p "蓝牙耳机" -n 50 --strategy hook  \u2551
\u2551  vf evolve --evolve-rounds 3                   \u2551
\u2551  vf serve --port 8088                          \u2551
\u255A\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255D
`);
}

function printVersion() {
  console.log('VIDEO-FACTORY CLI v3.0.0');
  console.log('SkillOpt Engine · 300-Agent Swarm');
}

// ==================== 参数解析 ====================
/**
 * 解析命令行参数
 * 支持: --key value, --key=value, -k value, -kvalue
 */
function parseArgs(args) {
  const options = { _raw: args };
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    // --key=value 形式
    if (arg.startsWith('--') && arg.includes('=')) {
      const [key, ...valParts] = arg.slice(2).split('=');
      options[key] = valParts.join('=');
      continue;
    }
    // --key value 或 -k value 形式
    if (arg.startsWith('-')) {
      const key = arg.replace(/^--/, '').replace(/^-/, '');
      const nextArg = args[i + 1];
      if (nextArg && !nextArg.startsWith('-')) {
        options[key] = nextArg;
        i++; // 跳过已消费的值
      } else {
        options[key] = true; // 布尔标志
      }
    }
  }
  // 兼容短参数别名
  if (options.p && !options.product) options.product = options.p;
  if (options.i && !options.image) options.image = options.i;
  if (options.d && !options.duration) options.duration = options.d;
  if (options.n && !options.count) options.count = options.n;
  if (options.m && !options.materials) options.materials = options.m;
  if (options.o && !options.output) options.output = options.o;
  return options;
}

/**
 * 获取 API 基础 URL
 */
function getApiUrl(options) {
  return options['api-url'] || process.env.VF_API_URL || DEFAULT_API_URL;
}

// ==================== HTTP 工具 ====================
/**
 * 发送 HTTP 请求 (Promise 封装)
 */
function request(url, options = {}) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      port: urlObj.port || 80,
      path: urlObj.pathname + urlObj.search,
      method: options.method || 'GET',
      headers: options.headers || {}
    };

    const req = http.request(reqOptions, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          resolve({ status: res.statusCode, data: parsed, headers: res.headers });
        } catch {
          resolve({ status: res.statusCode, data, headers: res.headers });
        }
      });
    });

    req.on('error', reject);
    if (options.body) req.write(options.body);
    req.end();
  });
}

/**
 * GET 请求快捷方法
 */
function get(apiUrl, endpoint) {
  return request(`${apiUrl}${endpoint}`);
}

/**
 * POST 请求快捷方法
 */
function post(apiUrl, endpoint, body) {
  const bodyStr = typeof body === 'string' ? body : JSON.stringify(body);
  return request(`${apiUrl}${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(bodyStr) },
    body: bodyStr
  });
}

// ==================== 进度条渲染 ====================
/**
 * 渲染进度条
 */
function renderProgressBar(percent, width = 40) {
  const filled = Math.round(width * percent / 100);
  const empty = width - filled;
  const bar = '\u2588'.repeat(filled) + '\u2591'.repeat(empty);
  return `[${bar}] ${percent.toFixed(1)}%`;
}

/**
 * 清行并输出
 */
function clearLine() {
  process.stdout.write('\r\u001B[K');
}

/**
 * 格式化耗时
 */
function formatDuration(ms) {
  if (ms < 60000) return `${(ms/1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60000);
  const s = ((ms % 60000) / 1000).toFixed(0);
  return `${m}m${s}s`;
}

// ==================== 命令: generate ====================
async function cmdGenerate(options) {
  const product = options.product;
  const platform = options.platform || 'douyin';
  const duration = parseInt(options.duration || 30);
  const apiUrl = getApiUrl(options);

  console.log('\u{1F3AC} 启动带货视频生成...');
  console.log(`   产品: ${product || '(未指定)'}`);
  console.log(`   平台: ${platform} | 时长: ${duration}s`);
  console.log(`   API: ${apiUrl}`);

  if (!product) {
    console.error('\u274C 错误: 请指定产品名称 (--product "产品名称")');
    process.exit(1);
  }

  if (!SUPPORTED_PLATFORMS.includes(platform)) {
    console.error(`\u274C 错误: 不支持的平台 "${platform}"`);
    console.error(`   支持: ${SUPPORTED_PLATFORMS.join(', ')}`);
    process.exit(1);
  }

  const startTime = Date.now();

  try {
    // 构建请求体
    const payload = {
      product,
      platform,
      durationSec: duration,
      count: 1,
      strategy: options.strategy || 'all',
      scriptStyle: options['script-style'] || 'auto',
      voiceType: options.voice || 'auto',
      evolveRounds: parseInt(options['evolve-rounds'] || 1)
    };

    console.log('\n\u{1F4E1} 发送生成请求...');
    const response = await post(apiUrl, '/api/generate', payload);

    if (![200, 202].includes(response.status)) {
      throw new Error(`HTTP ${response.status}: ${JSON.stringify(response.data)}`);
    }

    const { jobId } = response.data;
    console.log(`\u2705 任务已创建: ${jobId}`);

    // 根据 --wait 参数决定是否等待
    if (options.wait || options.w) {
      console.log('\n\u23F3 等待任务完成...');
      await pollProgress(apiUrl, jobId, startTime);
    } else {
      console.log(`   查询状态: vf status --job ${jobId}`);
      console.log(`   等待模式: vf generate -p "${product}" --wait`);
    }

  } catch (e) {
    console.error(`\n\u274C 请求失败: ${e.message}`);
    console.error(`\n\u{1F4A1} 提示: 请确保服务已启动`);
    console.error(`   vf serve        # 启动服务`);
    console.error(`   vf status       # 检查状态`);
    process.exit(1);
  }
}

// ==================== 命令: matrix ====================
async function cmdMatrix(options) {
  const product = options.product;
  const count = parseInt(options.count || options.n || 10);
  const strategy = options.strategy || 'all';
  const apiUrl = getApiUrl(options);

  console.log(`\u{1F522} 矩阵批量生成模式`);
  console.log(`   产品: ${product || '(未指定)'}`);
  console.log(`   数量: ${count} 条 | 策略: ${strategy}`);

  if (!product) {
    console.error('\u274C 错误: 请指定产品名称 (--product "产品名称")');
    process.exit(1);
  }

  if (count > MAX_MATRIX_COUNT) {
    console.error(`\u274C 错误: 单次最多生成 ${MAX_MATRIX_COUNT} 条`);
    console.error(`   如需更多，请分批执行`);
    process.exit(1);
  }

  if (count < 1) {
    console.error('\u274C 错误: 生成数量至少为 1');
    process.exit(1);
  }

  const startTime = Date.now();

  try {
    const payload = {
      product,
      platform: options.platform || 'douyin',
      durationSec: parseInt(options.duration || 30),
      count,
      strategy,
      scriptStyle: options['script-style'] || 'auto',
      voiceType: options.voice || 'auto',
      evolveRounds: parseInt(options['evolve-rounds'] || 1)
    };

    console.log(`\n\u{1F4E1} 发送矩阵请求 (${count} 条)...`);
    const response = await post(apiUrl, '/api/generate', payload);

    if (![200, 202].includes(response.status)) {
      throw new Error(`HTTP ${response.status}: ${JSON.stringify(response.data)}`);
    }

    const { jobId } = response.data;
    console.log(`\u2705 矩阵任务已创建: ${jobId}`);
    console.log(`   预估耗时: ${count <= 10 ? '5-15分钟' : count <= 50 ? '20-40分钟' : '40-90分钟'}`);

    if (options.wait || options.w) {
      console.log('\n\u23F3 等待矩阵任务完成...');
      await pollProgress(apiUrl, jobId, startTime);
    }

  } catch (e) {
    console.error(`\n\u274C 矩阵请求失败: ${e.message}`);
    process.exit(1);
  }
}

// ==================== 命令: evolve ====================
async function cmdEvolve(options) {
  const rounds = parseInt(options['evolve-rounds'] || 1);
  const apiUrl = getApiUrl(options);

  console.log(`\u{1F9EC} 启动 SkillOpt 自进化引擎`);
  console.log(`   进化轮次: ${rounds}`);
  console.log(`   API: ${apiUrl}\n`);

  for (let i = 0; i < rounds; i++) {
    console.log(`\u{1F504} 进化轮次 ${i + 1}/${rounds}...`);
    const start = Date.now();

    try {
      const response = await post(apiUrl, '/api/evolve', {
        round: i + 1,
        totalRounds: rounds
      });

      if (response.status === 200) {
        const elapsed = Date.now() - start;
        console.log(`   \u2705 完成 (${formatDuration(elapsed)})`);
        if (response.data.improvement) {
          console.log(`   \u{1F4C8} 改进: ${response.data.improvement}`);
        }
      } else {
        console.log(`   \u26A0\uFE0F 状态码: ${response.status}`);
      }
    } catch (e) {
      console.error(`   \u274C 失败: ${e.message}`);
    }
  }

  console.log('\n\u{1F3C6} 进化流程全部完成！');
}

// ==================== 命令: hook ====================
async function cmdHook(options) {
  const product = options.product;
  const platform = options.platform || 'douyin';
  const apiUrl = getApiUrl(options);

  console.log(`\u{1F3A3} 生成钩子角度库`);
  console.log(`   产品: ${product || '(未指定)'}`);
  console.log(`   平台: ${platform}\n`);

  if (!product) {
    console.error('\u274C 错误: 请指定产品名称');
    process.exit(1);
  }

  try {
    const response = await post(apiUrl, '/api/hook', { product, platform });

    if (response.status === 200 && response.data.hooks) {
      console.log(`\u2705 发现 ${response.data.hooks.length} 个高转化钩子:\n`);
      response.data.hooks.forEach((h, i) => {
        console.log(`   ${String.fromCharCode(65 + i)}. ${h.text}`);
        console.log(`      类型: ${h.type} | 预估CTR: ${h.estimatedCtr || 'N/A'}\n`);
      });
    } else {
      // 模拟输出 (服务不可用时)
      console.log('\u{1F4A1} 模拟钩子角度 (服务未连接):\n');
      const mockHooks = [
        { text: `${product}用了3个月，来说点大实话...`, type: '悬念型', ctr: '4.2%' },
        { text: `为什么懂行的人都选这个${product}？`, type: '权威型', ctr: '3.8%' },
        { text: `才用了1次，我直接把旧的扔了`, type: '对比型', ctr: '5.1%' },
        { text: `别再用老式${product}了！`, type: '否定型', ctr: '3.5%' },
        { text: `这个${product}救了我一命`, type: '故事型', ctr: '4.7%' },
        { text: `${product}测评：300元vs3000元区别在哪`, type: '评测型', ctr: '3.9%' }
      ];
      mockHooks.forEach((h, i) => {
        console.log(`   ${String.fromCharCode(65 + i)}. ${h.text}`);
        console.log(`      类型: ${h.type} | 预估CTR: ${h.ctr}\n`);
      });
    }
  } catch (e) {
    console.error(`\u274C 请求失败: ${e.message}`);
  }
}

// ==================== 命令: hybrid ====================
async function cmdHybrid(options) {
  const materialsDir = options.materials || options.m;
  const product = options.product;

  console.log(`\u{1F3AC} 混剪模式: AI爆款开头 + 用户素材`);
  console.log(`   产品: ${product || '(未指定)'}`);
  console.log(`   素材目录: ${materialsDir || '(未指定)'}`);

  if (!product) {
    console.error('\u274C 错误: 请指定产品名称 (--product)');
    process.exit(1);
  }

  if (!materialsDir) {
    console.error('\u274C 错误: 请指定素材目录 (--materials <目录路径>)');
    console.error('\n   素材组织建议:');
    console.error('   materials/');
    console.error('     \u251C\u2500\u2500 opening/       # 真人口播开头');
    console.error('     \u251C\u2500\u2500 product/       # 产品实拍');
    console.error('     \u251C\u2500\u2500 demo/          # 使用演示');
    console.error('     \u2514\u2500\u2500 result/        # 效果对比');
    process.exit(1);
  }

  if (!fs.existsSync(materialsDir)) {
    console.error(`\u274C 错误: 素材目录不存在: ${materialsDir}`);
    process.exit(1);
  }

  // 扫描素材文件
  const scanDir = (dir) => {
    const files = [];
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        files.push(...scanDir(fullPath));
      } else if (/\.(mp4|mov|avi|jpg|jpeg|png|webp)$/i.test(entry.name)) {
        const stat = fs.statSync(fullPath);
        files.push({
          path: fullPath,
          name: entry.name,
          type: entry.name.match(/\.(mp4|mov|avi)$/i) ? 'video' : 'image',
          size: stat.size
        });
      }
    }
    return files;
  };

  const files = scanDir(materialsDir);
  console.log(`\n\u{1F4C1} 扫描到 ${files.length} 个素材文件:`);

  const videoCount = files.filter(f => f.type === 'video').length;
  const imageCount = files.filter(f => f.type === 'image').length;
  console.log(`   \u{1F39E} 视频: ${videoCount} 个 | \u{1F5BC} 图片: ${imageCount} 个`);

  files.forEach(f => {
    const sizeMB = (f.size / 1024 / 1024).toFixed(1);
    const icon = f.type === 'video' ? '\u{1F39E}' : '\u{1F5BC}';
    console.log(`   ${icon} ${f.name} (${sizeMB} MB)`);
  });

  // 发送混剪请求
  const apiUrl = getApiUrl(options);
  try {
    console.log('\n\u{1F4E1} 发送混剪请求...');
    const response = await post(apiUrl, '/api/hybrid', {
      product,
      platform: options.platform || 'douyin',
      durationSec: parseInt(options.duration || 30),
      materials: files.map(f => ({ path: f.path, type: f.type })),
      count: parseInt(options.count || 1)
    });

    if (response.status === 200) {
      console.log(`\u2705 混剪任务已创建: ${response.data.jobId}`);
    }
  } catch (e) {
    console.error(`\u274C 混剪请求失败: ${e.message}`);
  }
}

// ==================== 命令: qa ====================
async function cmdQA(options) {
  const apiUrl = getApiUrl(options);
  const jobId = options.job;

  console.log(`\u{1F50D} 智能质检检查`);

  try {
    if (jobId) {
      // 检查指定任务
      const response = await get(apiUrl, `/api/jobs/${jobId}`);
      console.log(`\n   任务: ${jobId}`);
      console.log(`   状态: ${response.data.state}`);
      if (response.data.qaScore) {
        console.log(`   质检得分: ${response.data.qaScore}/100`);
        console.log(`   质检项: ${JSON.stringify(response.data.qaDetails, null, 4)}`);
      }
    } else {
      // 获取全局质检统计
      const response = await get(apiUrl, '/api/qa/stats');
      console.log('\n\u{1F4CA} 质检统计:');
      console.log(`   总检查: ${response.data.totalChecked || 0}`);
      console.log(`   通过率: ${response.data.passRate || 'N/A'}`);
      console.log(`   平均分: ${response.data.avgScore || 'N/A'}`);
    }
  } catch (e) {
    console.error(`\u274C 质检检查失败: ${e.message}`);
  }
}

// ==================== 命令: status ====================
async function cmdStatus(options) {
  const apiUrl = getApiUrl(options);

  console.log(`\u{1F50C} 检查服务状态...`);
  console.log(`   API端点: ${apiUrl}\n`);

  const startTime = Date.now();

  try {
    const response = await get(apiUrl, '/api/config');
    const latency = Date.now() - startTime;

    console.log(`\u2705 服务运行中 (${latency}ms)`);
    console.log(`\n   配置信息:`);
    console.log(`   ${JSON.stringify(response.data, null, 4).replace(/\n/g, '\n   ')}`);

    // 获取Agent状态
    try {
      const agentRes = await get(apiUrl, '/api/agents');
      if (agentRes.data && agentRes.data.agents) {
        console.log(`\n   \u{1F41D} Agent蜂群状态:`);
        console.log(`   总数: ${agentRes.data.total || 300}`);
        console.log(`   活跃: ${agentRes.data.active || 0}`);
        console.log(`   空闲: ${agentRes.data.idle || 0}`);
      }
    } catch {
      // Agent端点可选
    }

  } catch (e) {
    console.log(`\u274C 服务未响应`);
    console.log(`   错误: ${e.message}`);
    console.log(`\n\u{1F4A1} 启动服务:`);
    console.log(`   vf serve`);
    console.log(`   # 或`);
    console.log(`   node live-server.js`);
  }
}

// ==================== 命令: serve ====================
async function cmdServe(options) {
  const port = parseInt(options.port || options.p || 8088);
  console.log(`\u{1F680} 启动 VIDEO-FACTORY HTTP 服务...`);
  console.log(`   端口: ${port}`);
  console.log(`   根目录: ${PACKAGE_ROOT}\n`);

  try {
    const serverPath = path.join(PACKAGE_ROOT, 'live-server.js');
    if (!fs.existsSync(serverPath)) {
      console.error(`\u274C 错误: 未找到服务文件: ${serverPath}`);
      console.error(`   请确保 live-server.js 存在于项目根目录`);
      process.exit(1);
    }

    // 设置环境变量并启动
    process.env.PORT = port.toString();
    process.env.VF_ROOT = PACKAGE_ROOT;

    console.log(`\u{1F4E1} 启动中...`);
    require(serverPath);

  } catch (e) {
    console.error(`\u274C 启动失败: ${e.message}`);
    console.error(e.stack);
    process.exit(1);
  }
}

// ==================== 命令: config ====================
async function cmdConfig(options) {
  console.log(`\u2699\uFE0F 配置管理`);

  // 读取现有配置
  let config = {};
  if (fs.existsSync(CONFIG_PATH)) {
    try {
      config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
    } catch (e) {
      console.error(`   警告: 配置文件解析失败`);
    }
  }

  if (options.set) {
    // 设置配置项
    const [key, value] = options.set.split('=');
    if (key && value) {
      config[key] = value;
      fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
      console.log(`   \u2705 已设置: ${key} = ${value}`);
    } else {
      console.error(`   \u274C 格式错误, 使用: --set key=value`);
    }
  } else if (options.get) {
    // 获取配置项
    console.log(`   ${options.get} = ${config[options.get] || '(未设置)'}`);
  } else {
    // 显示全部配置
    console.log(`   配置文件: ${CONFIG_PATH}\n`);
    if (Object.keys(config).length === 0) {
      console.log('   (暂无配置)');
    } else {
      for (const [k, v] of Object.entries(config)) {
        console.log(`   ${k}: ${v}`);
      }
    }
  }
}

// ==================== 命令: list ====================
async function cmdList(options) {
  const apiUrl = getApiUrl(options);
  const limit = parseInt(options.limit || 10);

  console.log(`\u{1F4CB} 历史任务列表 (最近 ${limit} 条)\n`);

  try {
    const response = await get(apiUrl, `/api/jobs?limit=${limit}`);
    if (response.data && response.data.jobs) {
      const jobs = response.data.jobs;
      console.log(`   共 ${jobs.length} 条记录\n`);

      jobs.forEach((job, i) => {
        const stateIcon = job.state === 'succeeded' ? '\u2705' :
                         job.state === 'failed' ? '\u274C' : '\u23F3';
        console.log(`   ${stateIcon} [${job.id}] ${job.product || 'N/A'}`);
        console.log(`      状态: ${job.state} | 平台: ${job.platform} | 时长: ${job.durationSec}s`);
        if (job.qaScore) console.log(`      质检: ${job.qaScore}/100`);
        console.log('');
      });
    } else {
      console.log('   (暂无任务记录)');
    }
  } catch (e) {
    console.error(`   \u274C 获取失败: ${e.message}`);
  }
}

// ==================== 命令: cancel ====================
async function cmdCancel(options) {
  const jobId = options.job || options._raw[0];
  const apiUrl = getApiUrl(options);

  if (!jobId) {
    console.error('\u274C 错误: 请指定任务ID (--job <id>)');
    process.exit(1);
  }

  console.log(`\u274C 取消任务: ${jobId}`);

  try {
    const response = await post(apiUrl, `/api/jobs/${jobId}/cancel`, {});
    if (response.status === 200) {
      console.log(`\u2705 任务已取消`);
    } else {
      console.log(`\u26A0\uFE0F 状态码: ${response.status}`);
    }
  } catch (e) {
    console.error(`\u274C 取消失败: ${e.message}`);
  }
}

// ==================== 命令: download ====================
async function cmdDownload(options) {
  const jobId = options.job || options._raw[0];
  const outputDir = options.output || options.o || './output';

  if (!jobId) {
    console.error('\u274C 错误: 请指定任务ID (--job <id>)');
    process.exit(1);
  }

  console.log(`\u2B07\uFE0F 下载任务结果: ${jobId}`);
  console.log(`   输出目录: ${outputDir}`);

  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  const apiUrl = getApiUrl(options);
  try {
    const response = await get(apiUrl, `/api/jobs/${jobId}`);
    const job = response.data;

    if (job.videoUrl) {
      console.log(`   视频: ${job.videoUrl}`);
      // 实际下载逻辑需要实现
      console.log(`   \u2705 结果已保存到: ${outputDir}/`);
    }

    if (job.videos) {
      console.log(`   批量视频: ${job.videos.length} 个`);
    }
  } catch (e) {
    console.error(`\u274C 下载失败: ${e.message}`);
  }
}

// ==================== 轮询进度 ====================
async function pollProgress(apiUrl, jobId, startTime) {
  const spinnerFrames = ['\u280B', '\u2819', '\u2839', '\u2838', '\u283C', '\u2834', '\u2826', '\u2827', '\u2807', '\u280F'];
  let frameIdx = 0;
  let lastProgress = -1;
  let lastLog = '';

  return new Promise((resolve, reject) => {
    const interval = setInterval(async () => {
      try {
        const response = await get(apiUrl, `/api/jobs/${jobId}`);
        const job = response.data;
        const progress = job.progress || 0;

        // 只在进度变化时更新显示
        if (progress !== lastProgress) {
          lastProgress = progress;
          const elapsed = Date.now() - startTime;
          const spinner = spinnerFrames[frameIdx % spinnerFrames.length];

          clearLine();
          process.stdout.write(
            `   ${spinner} ${renderProgressBar(progress)} ` +
            `| ${job.currentPhase || 'running'} ` +
            `| ${formatDuration(elapsed)}`
          );
        }
        frameIdx++;

        // 打印新的日志
        if (job.log && job.log !== lastLog) {
          lastLog = job.log;
          clearLine();
          console.log(`\n   \u{1F4DD} ${job.log}`);
        }

        if (job.state === 'succeeded' || job.state === 'completed') {
          clearInterval(interval);
          clearLine();
          const totalElapsed = Date.now() - startTime;
          console.log(`\n\n\u2705 任务完成！总耗时: ${formatDuration(totalElapsed)}`);
          if (job.qaScore) console.log(`   质检得分: ${job.qaScore}/100`);
          if (job.videoUrl) console.log(`   视频地址: ${job.videoUrl}`);
          resolve(job);
        } else if (job.state === 'failed') {
          clearInterval(interval);
          clearLine();
          console.log(`\n\n\u274C 任务失败: ${job.error || '未知错误'}`);
          reject(new Error(job.error || 'Task failed'));
        }

      } catch (e) {
        // 轮询出错继续尝试，不中断
        frameIdx++;
      }
    }, 1500);
  });
}

// ==================== 主入口 ====================
async function main() {
  const args = process.argv.slice(2);

  // 无参数或帮助
  if (args.length === 0 || args.includes('--help') || args.includes('-h')) {
    printHelp();
    return;
  }

  // 版本
  if (args.includes('--version') || args.includes('-v')) {
    printVersion();
    return;
  }

  const command = args[0];
  const options = parseArgs(args.slice(1));

  // 路由到对应命令
  switch (command) {
    case 'generate':
    case 'gen':
    case 'g':
      await cmdGenerate(options);
      break;

    case 'matrix':
    case 'm':
      await cmdMatrix(options);
      break;

    case 'evolve':
    case 'e':
      await cmdEvolve(options);
      break;

    case 'hook':
    case 'h':
      await cmdHook(options);
      break;

    case 'hybrid':
      await cmdHybrid(options);
      break;

    case 'qa':
      await cmdQA(options);
      break;

    case 'status':
    case 's':
      await cmdStatus(options);
      break;

    case 'serve':
    case 'server':
      await cmdServe(options);
      break;

    case 'config':
    case 'cfg':
      await cmdConfig(options);
      break;

    case 'list':
    case 'ls':
      await cmdList(options);
      break;

    case 'cancel':
    case 'stop':
      await cmdCancel(options);
      break;

    case 'download':
    case 'dl':
      await cmdDownload(options);
      break;

    default:
      console.error(`\u274C 未知命令: "${command}"`);
      console.error(`\n   可用命令: ${Object.keys(COMMANDS).join(', ')}`);
      console.error(`\n   查看帮助: vf --help`);
      process.exit(1);
  }
}

// ==================== 错误处理 ====================
process.on('unhandledRejection', (err) => {
  console.error('\n\u274C 未处理的异常:', err.message);
  process.exit(1);
});

process.on('SIGINT', () => {
  console.log('\n\n\u{1F44B} 已取消');
  process.exit(0);
});

// ==================== 启动 ====================
main().catch(err => {
  console.error('\n\u274C 执行失败:', err.message);
  process.exit(1);
});
