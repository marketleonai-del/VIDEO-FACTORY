/**
 * =============================================================================
 * VIDEO-FACTORY HQ - 数据收集服务 (collector.js)
 * =============================================================================
 * 
 * 职责：接收所有Agent节点的匿名遥测数据，提供参数查询和管理接口
 * 
 * 核心端点：
 *   POST /telemetry        - Agent上报隐式质量信号
 *   GET  /params/latest    - 查询当前冠军参数
 *   GET  /admin/dashboard  - 管理看板（按版本聚合质量）
 *   POST /admin/promote    - 手动晋升参数版本（需鉴权）
 *   POST /admin/candidate  - 注册候选参数
 * 
 * 安全机制：
 *   - anonId 限频（令牌桶算法）
 *   - 日配额检查（每anonId每日最多100条）
 *   - 二次脱敏（敏感字段哈希化）
 *   - 异常检测（统计离群值自动丢弃）
 * 
 * 技术栈：纯 Node.js (http/fs/crypto)，零运行时依赖
 * =============================================================================
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

// ============================================================================
// 配置常量
// ============================================================================

/** 服务监听端口 */
const PORT = process.env.HQ_PORT || 7300;
/** 管理接口密钥（环境变量注入，切勿硬编码） */
const ADMIN_KEY = process.env.HQ_ADMIN_KEY || 'dev-key-change-in-prod';
/** 每个匿名ID的日配额上限 */
const DAILY_QUOTA = 100;
/** 令牌桶限频：每秒最大请求数 */
const RATE_LIMIT_RPS = 2;
/** 令牌桶容量 */
const RATE_LIMIT_BURST = 5;
/** 异常检测：超出该标准差倍数的值视为离群 */
const OUTLIER_ZSCORE = 3.5;
/** 数据持久化路径 */
const DATA_DIR = process.env.HQ_DATA_DIR || './hq/data';
/** 冠军参数持久化文件 */
const CHAMPIONS_FILE = path.join(DATA_DIR, 'champions.json');
/** 遥测日志文件 */
const TELEMETRY_LOG = path.join(DATA_DIR, 'telemetry.log');

// ============================================================================
// 内存存储
// ============================================================================

/** 遥测事件存储（生产环境应替换为时序数据库如InfluxDB/TimescaleDB） */
const telemetryStore = [];

/** 冠军参数映射：version -> {params, savedAt, promotedAt, samples} */
const champions = new Map();

/** 候选参数映射：version -> {params, registeredAt, source} */
const candidates = new Map();

/** 限频状态：anonId -> {tokens, lastRefill, dailyCount, resetAt} */
const rateLimitMap = new Map();

// ============================================================================
// 工具函数
// ============================================================================

/**
 * 生成当前时间戳（ISO 8601）
 * @returns {string} ISO格式时间字符串
 */
function now() {
  return new Date().toISOString();
}

/**
 * 生成唯一请求ID（用于日志追踪）
 * @returns {string} 16字符十六进制随机字符串
 */
function genReqId() {
  return crypto.randomBytes(8).toString('hex');
}

/**
 * 计算单向哈希（用于anonId二次脱敏）
 * @param {string} input - 原始输入
 * @param {string} salt - 盐值
 * @returns {string} SHA-256哈希值
 */
function hashAnonId(input, salt = 'hq-salt-v1') {
  return crypto.createHash('sha256').update(input + salt).digest('hex').slice(0, 16);
}

/**
 * 安全脱敏遥测数据
 * @param {Object} data - 原始遥测数据
 * @returns {Object} 脱敏后的数据
 */
function desensitize(data) {
  const clone = JSON.parse(JSON.stringify(data));
  // 对 anonId 进行二次哈希脱敏
  if (clone.anonId) {
    clone.anonId = hashAnonId(clone.anonId);
  }
  // 移除任何可能包含PII的字段
  delete clone.ip;
  delete clone.userAgent;
  delete clone.hostname;
  return clone;
}

/**
 * 令牌桶限频检查
 * @param {string} anonId - 匿名标识
 * @returns {{allowed: boolean, remaining: number, retryAfter?: number}} 检查结果
 */
function checkRateLimit(anonId) {
  const nowMs = Date.now();
  let state = rateLimitMap.get(anonId);

  if (!state) {
    state = {
      tokens: RATE_LIMIT_BURST,
      lastRefill: nowMs,
      dailyCount: 0,
      resetAt: new Date(nowMs).setHours(24, 0, 0, 0)
    };
    rateLimitMap.set(anonId, state);
  }

  // 日配额重置检查
  if (nowMs >= state.resetAt) {
    state.dailyCount = 0;
    state.resetAt = new Date(nowMs).setHours(24, 0, 0, 0);
  }

  // 日配额超限
  if (state.dailyCount >= DAILY_QUOTA) {
    return { allowed: false, remaining: 0, retryAfter: Math.ceil((state.resetAt - nowMs) / 1000) };
  }

  // 令牌桶填充
  const elapsed = (nowMs - state.lastRefill) / 1000;
  state.tokens = Math.min(RATE_LIMIT_BURST, state.tokens + elapsed * RATE_LIMIT_RPS);
  state.lastRefill = nowMs;

  if (state.tokens < 1) {
    return { allowed: false, remaining: 0, retryAfter: Math.ceil((1 - state.tokens) / RATE_LIMIT_RPS) };
  }

  state.tokens -= 1;
  state.dailyCount += 1;
  return { allowed: true, remaining: Math.floor(state.tokens) };
}

/**
 * 异常检测：Z-Score方法判断值是否为离群
 * @param {number} value - 待检测值
 * @param {number} mean - 历史均值
 * @param {number} std - 历史标准差
 * @returns {boolean} 是否为离群值
 */
function isOutlier(value, mean, std) {
  if (std === 0) return false;
  const zScore = Math.abs((value - mean) / std);
  return zScore > OUTLIER_ZSCORE;
}

/**
 * 异步读取请求体
 * @param {http.IncomingMessage} req - HTTP请求对象
 * @returns {Promise<Object>} 解析后的JSON对象
 */
function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (e) {
        reject(new Error('Invalid JSON body: ' + e.message));
      }
    });
    req.on('error', reject);
  });
}

/**
 * 发送JSON响应
 * @param {http.ServerResponse} res - HTTP响应对象
 * @param {number} statusCode - HTTP状态码
 * @param {Object} payload - 响应体
 */
function jsonResponse(res, statusCode, payload) {
  res.writeHead(statusCode, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(payload));
}

/**
 * 记录日志到文件（追加模式，带自动旋转）
 * @param {string} level - 日志级别
 * @param {string} message - 日志内容
 * @param {Object} meta - 元数据
 */
function log(level, message, meta = {}) {
  const entry = {
    time: now(),
    level,
    message,
    ...meta
  };
  const line = JSON.stringify(entry) + '\n';

  // 控制台输出
  const colorMap = { INFO: '\x1b[32m', WARN: '\x1b[33m', ERROR: '\x1b[31m', DEBUG: '\x1b[36m' };
  const reset = '\x1b[0m';
  console.log(`${colorMap[level] || ''}[${entry.time}] [${level}] ${message}${reset}`, meta.reqId ? `(req:${meta.reqId})` : '');

  // 文件持久化（非阻塞）
  fs.appendFile(TELEMETRY_LOG, line, (err) => {
    if (err) console.error('[ERROR] 日志写入失败:', err.message);
  });
}

// ============================================================================
// 持久化管理
// ============================================================================

/**
 * 确保数据目录存在
 */
function ensureDataDir() {
  if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
    log('INFO', `数据目录已创建: ${DATA_DIR}`);
  }
}

/**
 * 加载持久化的冠军参数
 */
function loadChampions() {
  try {
    if (fs.existsSync(CHAMPIONS_FILE)) {
      const data = JSON.parse(fs.readFileSync(CHAMPIONS_FILE, 'utf8'));
      for (const [version, record] of data) {
        champions.set(version, record);
      }
      log('INFO', `已加载 ${champions.size} 个冠军参数版本`);
    }
  } catch (err) {
    log('WARN', '冠军参数加载失败，将使用空集合', { error: err.message });
  }
}

/**
 * 保存冠军参数到磁盘
 */
function persistChampions() {
  try {
    fs.writeFileSync(CHAMPIONS_FILE, JSON.stringify([...champions], null, 2));
  } catch (err) {
    log('ERROR', '冠军参数持久化失败', { error: err.message });
  }
}

// ============================================================================
// 业务逻辑处理
// ============================================================================

/**
 * 处理遥测上报
 * @param {http.IncomingMessage} req - 请求
 * @param {http.ServerResponse} res - 响应
 * @param {string} reqId - 请求ID
 */
async function handleTelemetry(req, res, reqId) {
  try {
    const body = await readBody(req);

    // 1. 基础校验
    if (!body.anonId || typeof body.anonId !== 'string') {
      return jsonResponse(res, 400, { error: '缺少 anonId 字段', reqId });
    }
    if (!body.paramsVersion || typeof body.paramsVersion !== 'string') {
      return jsonResponse(res, 400, { error: '缺少 paramsVersion 字段', reqId });
    }
    if (typeof body.qualityScore !== 'number' || body.qualityScore < 0 || body.qualityScore > 1) {
      return jsonResponse(res, 400, { error: 'qualityScore 必须是 [0,1] 范围内的数字', reqId });
    }

    // 2. 限频检查
    const rateCheck = checkRateLimit(body.anonId);
    if (!rateCheck.allowed) {
      log('WARN', '限频拒绝', { reqId, anonId: hashAnonId(body.anonId), retryAfter: rateCheck.retryAfter });
      res.setHeader('Retry-After', String(rateCheck.retryAfter));
      return jsonResponse(res, 429, { error: 'Rate limit exceeded', retryAfter: rateCheck.retryAfter, reqId });
    }

    // 3. 二次脱敏
    const cleanData = desensitize(body);

    // 4. 异常检测（基于该版本历史数据的Z-Score）
    const versionEvents = telemetryStore.filter(e => e.paramsVersion === cleanData.paramsVersion);
    if (versionEvents.length >= 10) {
      const scores = versionEvents.map(e => e.qualityScore);
      const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
      const variance = scores.reduce((a, b) => a + (b - mean) ** 2, 0) / scores.length;
      const std = Math.sqrt(variance);
      if (isOutlier(cleanData.qualityScore, mean, std)) {
        log('WARN', '异常值丢弃', { reqId, score: cleanData.qualityScore, mean: mean.toFixed(3), z: (Math.abs(cleanData.qualityScore - mean) / std).toFixed(2) });
        return jsonResponse(res, 202, { status: 'accepted', note: 'outlier_filtered', reqId });
      }
    }

    // 5. 构建完整事件记录
    const event = {
      ...cleanData,
      receivedAt: now(),
      reqId
    };

    // 6. 存入内存（生产环境应批量写入时序数据库）
    telemetryStore.push(event);

    // 7. 内存上限保护（防止OOM，保留最近50000条）
    const MAX_EVENTS = 50000;
    if (telemetryStore.length > MAX_EVENTS) {
      telemetryStore.splice(0, telemetryStore.length - MAX_EVENTS);
    }

    log('INFO', '遥测接收成功', {
      reqId,
      version: cleanData.paramsVersion,
      score: cleanData.qualityScore,
      remaining: rateCheck.remaining,
      totalStored: telemetryStore.length
    });

    jsonResponse(res, 200, {
      status: 'accepted',
      remainingQuota: rateCheck.remaining,
      totalEvents: telemetryStore.length,
      reqId
    });

  } catch (err) {
    log('ERROR', '遥测处理异常', { reqId, error: err.message });
    jsonResponse(res, 500, { error: 'Internal server error', reqId });
  }
}

/**
 * 返回当前冠军参数
 * @param {http.ServerResponse} res - 响应
 * @param {string} reqId - 请求ID
 */
function handleGetParams(res, reqId) {
  // 获取最新版本（按promotedAt倒序）
  let latestVersion = null;
  let latestRecord = null;
  for (const [version, record] of champions) {
    if (!latestRecord || (record.promotedAt || 0) > (latestRecord.promotedAt || 0)) {
      latestVersion = version;
      latestRecord = record;
    }
  }

  if (!latestRecord) {
    return jsonResponse(res, 200, {
      version: 'v0-default',
      params: getDefaultParams(),
      updatedAt: now(),
      note: 'using_fallback_defaults',
      reqId
    });
  }

  jsonResponse(res, 200, {
    version: latestVersion,
    params: latestRecord.params,
    updatedAt: new Date(latestRecord.promotedAt).toISOString(),
    samples: latestRecord.samples || 0,
    reqId
  });
}

/**
 * 获取默认参数（首次启动无冠军时使用）
 * @returns {Object} 默认生成参数
 */
function getDefaultParams() {
  return {
    temperature: 0.85,
    topP: 0.9,
    presencePenalty: 0.3,
    frequencyPenalty: 0.5,
    maxTokens: 800,
    styleIntensity: 0.7,
    hookStrength: 0.8,
    captionDensity: 0.6,
    bgmVolume: 0.15,
    transitionStyle: 'smooth',
    aspectRatio: '9:16',
    resolution: '1080p',
    fps: 30
  };
}

/**
 * 管理看板：按版本聚合质量指标
 * @param {http.ServerResponse} res - 响应
 * @param {string} reqId - 请求ID
 */
function handleDashboard(res, reqId) {
  // 按版本分组统计
  const versionMap = new Map();

  for (const event of telemetryStore) {
    const v = event.paramsVersion;
    if (!versionMap.has(v)) {
      versionMap.set(v, { scores: [], count: 0, anonIds: new Set() });
    }
    const g = versionMap.get(v);
    g.scores.push(event.qualityScore);
    g.count += 1;
    g.anonIds.add(event.anonId);
  }

  // 计算统计指标
  const stats = [];
  for (const [version, g] of versionMap) {
    const scores = g.scores;
    scores.sort((a, b) => a - b);
    const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
    const variance = scores.reduce((a, b) => a + (b - mean) ** 2, 0) / scores.length;
    const median = scores[Math.floor(scores.length / 2)];
    const p25 = scores[Math.floor(scores.length * 0.25)];
    const p75 = scores[Math.floor(scores.length * 0.75)];

    const isChampion = champions.has(version);
    const championRecord = champions.get(version);

    stats.push({
      version,
      sampleCount: scores.length,
      uniqueAgents: g.anonIds.size,
      meanQuality: parseFloat(mean.toFixed(4)),
      medianQuality: parseFloat(median.toFixed(4)),
      p25Quality: parseFloat(p25.toFixed(4)),
      p75Quality: parseFloat(p75.toFixed(4)),
      stdDev: parseFloat(Math.sqrt(variance).toFixed(4)),
      isChampion,
      promotedAt: championRecord ? new Date(championRecord.promotedAt).toISOString() : null,
      status: isChampion ? 'champion' : (candidates.has(version) ? 'candidate' : 'unknown')
    });
  }

  // 按均值降序排列
  stats.sort((a, b) => b.meanQuality - a.meanQuality);

  // 添加趋势判断（与冠军版本对比）
  const championStat = stats.find(s => s.isChampion);
  if (championStat) {
    for (const s of stats) {
      if (s.version === championStat.version) {
        s.trend = 'baseline';
      } else if (s.meanQuality > championStat.meanQuality) {
        s.trend = 'better';
        s.delta = parseFloat((s.meanQuality - championStat.meanQuality).toFixed(4));
      } else {
        s.trend = 'worse';
        s.delta = parseFloat((s.meanQuality - championStat.meanQuality).toFixed(4));
      }
    }
  }

  jsonResponse(res, 200, {
    generatedAt: now(),
    totalEvents: telemetryStore.length,
    totalVersions: stats.length,
    versions: stats,
    reqId
  });
}

/**
 * 管理鉴权检查
 * @param {http.IncomingMessage} req - 请求
 * @returns {boolean} 是否通过鉴权
 */
function checkAdminAuth(req) {
  const key = req.headers['x-admin-key'];
  return key === ADMIN_KEY;
}

/**
 * 晋升参数版本
 * @param {http.IncomingMessage} req - 请求
 * @param {http.ServerResponse} res - 响应
 * @param {string} reqId - 请求ID
 */
async function handlePromote(req, res, reqId) {
  if (!checkAdminAuth(req)) {
    log('WARN', '管理鉴权失败', { reqId, ip: req.socket.remoteAddress });
    return jsonResponse(res, 401, { error: 'Unauthorized: invalid x-admin-key', reqId });
  }

  try {
    const body = await readBody(req);
    const { version } = body;

    if (!version || typeof version !== 'string') {
      return jsonResponse(res, 400, { error: '缺少 version 字段', reqId });
    }

    const candidate = candidates.get(version);
    if (!candidate) {
      return jsonResponse(res, 404, { error: `候选版本 ${version} 不存在，请先注册`, reqId });
    }

    // 统计该版本的样本数
    const versionSamples = telemetryStore.filter(e => e.paramsVersion === version).length;

    // 晋升操作
    const oldChampion = getCurrentChampionVersion();
    champions.set(version, {
      params: candidate.params,
      savedAt: Date.now(),
      promotedAt: Date.now(),
      samples: versionSamples,
      promotedBy: 'admin',
      previousVersion: oldChampion
    });

    persistChampions();

    log('INFO', '参数版本已晋升', { reqId, version, fromVersion: oldChampion, samples: versionSamples });

    jsonResponse(res, 200, {
      status: 'promoted',
      version,
      previousVersion: oldChampion,
      samples: versionSamples,
      promotedAt: now(),
      reqId
    });

  } catch (err) {
    log('ERROR', '晋升操作异常', { reqId, error: err.message });
    jsonResponse(res, 500, { error: 'Internal server error', reqId });
  }
}

/**
 * 注册候选参数
 * @param {http.IncomingMessage} req - 请求
 * @param {http.ServerResponse} res - 响应
 * @param {string} reqId - 请求ID
 */
async function handleCandidate(req, res, reqId) {
  if (!checkAdminAuth(req)) {
    return jsonResponse(res, 401, { error: 'Unauthorized: invalid x-admin-key', reqId });
  }

  try {
    const body = await readBody(req);
    const { version, params, source } = body;

    if (!version || typeof version !== 'string') {
      return jsonResponse(res, 400, { error: '缺少 version 字段', reqId });
    }
    if (!params || typeof params !== 'object') {
      return jsonResponse(res, 400, { error: '缺少 params 对象', reqId });
    }

    candidates.set(version, {
      params,
      registeredAt: Date.now(),
      source: source || 'manual'
    });

    log('INFO', '候选参数已注册', { reqId, version, source: source || 'manual' });

    jsonResponse(res, 200, {
      status: 'registered',
      version,
      candidateCount: candidates.size,
      registeredAt: now(),
      reqId
    });

  } catch (err) {
    log('ERROR', '候选注册异常', { reqId, error: err.message });
    jsonResponse(res, 500, { error: 'Internal server error', reqId });
  }
}

/**
 * 获取当前冠军版本号
 * @returns {string|null} 当前冠军版本，无则返回null
 */
function getCurrentChampionVersion() {
  let latestVersion = null;
  let latestRecord = null;
  for (const [version, record] of champions) {
    if (!latestRecord || (record.promotedAt || 0) > (latestRecord.promotedAt || 0)) {
      latestVersion = version;
      latestRecord = record;
    }
  }
  return latestVersion;
}

// ============================================================================
// HTTP 服务
// ============================================================================

/**
 * 路由分发器
 */
const server = http.createServer((req, res) => {
  // CORS 头
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, x-admin-key');

  // 预检请求
  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  const reqId = genReqId();
  const route = `${req.method} ${req.url}`;

  log('DEBUG', '请求到达', { reqId, route, ip: req.socket.remoteAddress });

  // 路由分发
  if (req.url === '/telemetry' && req.method === 'POST') {
    handleTelemetry(req, res, reqId);
  } else if (req.url === '/params/latest' && req.method === 'GET') {
    handleGetParams(res, reqId);
  } else if (req.url === '/admin/dashboard' && req.method === 'GET') {
    handleDashboard(res, reqId);
  } else if (req.url === '/admin/promote' && req.method === 'POST') {
    handlePromote(req, res, reqId);
  } else if (req.url === '/admin/candidate' && req.method === 'POST') {
    handleCandidate(req, res, reqId);
  } else if (req.url === '/health' && req.method === 'GET') {
    // 健康检查端点（负载均衡器使用）
    jsonResponse(res, 200, {
      status: 'healthy',
      uptime: process.uptime(),
      memory: process.memoryUsage(),
      eventsStored: telemetryStore.length,
      champions: champions.size,
      candidates: candidates.size,
      reqId
    });
  } else {
    jsonResponse(res, 404, { error: 'Not found', route, reqId });
  }
});

// ============================================================================
// 优雅关闭
// ============================================================================

process.on('SIGTERM', () => {
  log('INFO', '收到 SIGTERM，开始优雅关闭...');
  persistChampions();
  server.close(() => {
    log('INFO', 'HTTP服务已关闭');
    process.exit(0);
  });
});

process.on('SIGINT', () => {
  log('INFO', '收到 SIGINT，开始优雅关闭...');
  persistChampions();
  server.close(() => {
    log('INFO', 'HTTP服务已关闭');
    process.exit(0);
  });
});

// 未捕获异常保护
process.on('uncaughtException', (err) => {
  log('ERROR', '未捕获异常', { error: err.message, stack: err.stack });
  persistChampions();
  process.exit(1);
});

// ============================================================================
// 启动
// ============================================================================

ensureDataDir();
loadChampions();

server.listen(PORT, () => {
  log('INFO', `VIDEO-FACTORY HQ Collector 已启动`, { port: PORT, dataDir: DATA_DIR });
  log('INFO', `可用端点:`, {
    telemetry: `POST http://localhost:${PORT}/telemetry`,
    params: `GET http://localhost:${PORT}/params/latest`,
    dashboard: `GET http://localhost:${PORT}/admin/dashboard`,
    promote: `POST http://localhost:${PORT}/admin/promote`,
    candidate: `POST http://localhost:${PORT}/admin/candidate`,
    health: `GET http://localhost:${PORT}/health`
  });
});

// 导出模块（供测试和aggregate.js使用）
module.exports = {
  telemetryStore,
  champions,
  candidates,
  handleTelemetry,
  handleGetParams,
  handleDashboard,
  handlePromote,
  handleCandidate,
  checkRateLimit,
  isOutlier,
  hashAnonId,
  desensitize,
  server
};
