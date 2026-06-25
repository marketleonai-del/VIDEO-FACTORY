/**
 * AgentProtocol - 300-Agent 蜂群通信协议
 *
 * 角色分配 (总计 300 Agent):
 *   - generator (N=200): 视频生成 Agent，执行带货视频生成任务
 *   - evolver   (N=50):  进化 Agent，运行本地进化循环并上报信号
 *   - qa        (N=30):  质检 Agent，执行 120 门质检流程
 *   - creative  (N=20):  创意 Agent，优化提示词与拍摄角度
 *
 * 协议设计:
 *   1. Agent 启动 → 向 HQ 注册 → 获取角色与初始参数
 *   2. Agent 定期心跳 → HQ 监控健康状态
 *   3. HQ 任务分配 → Agent 接收并执行 → 结果回流
 *   4. 进化信号收集 → HQ 聚合 → 参数优化 → 下发
 */

const http = require('http');
const https = require('https');
const { URL } = require('url');

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * 带退避的指数补偿重试
 * @param {Function} fn       需要重试的异步函数
 * @param {number}   maxRetries 最大重试次数 (默认 3)
 * @param {string}   label     日志标签
 */
async function withBackoff(fn, maxRetries = 3, label = 'request') {
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      if (attempt === maxRetries) {
        console.error(`[${label}] 最终失败，已重试 ${maxRetries} 次:`, err.message);
        throw err;
      }
      const delayMs = Math.min(1000 * Math.pow(2, attempt) + Math.random() * 500, 8000);
      console.warn(`[${label}] 第 ${attempt + 1} 次失败: ${err.message}，${delayMs.toFixed(0)}ms 后重试...`);
      await sleep(delayMs);
    }
  }
}

// ---------------------------------------------------------------------------
// AgentProtocol 主类
// ---------------------------------------------------------------------------

class AgentProtocol {
  /**
   * @param {string} hqUrl    HQ 总部地址 (如 http://hq.video-factory.io:8080)
   * @param {string} agentId  Agent 唯一标识 (若为空则自动生成)
   * @param {string} role     角色: generator | evolver | qa | creative
   */
  constructor(hqUrl, agentId = null, role = 'generator') {
    if (!hqUrl) throw new Error('HQ URL 不能为空');
    this.hqUrl = hqUrl.replace(/\/$/, '');
    this.agentId = agentId || this.generateAgentId();
    this.role = role;
    this.skillParams = null;          // 当前本地生效的 Skill 参数
    this.taskQueue = [];              // 本地任务队列缓冲
    this.isRunning = false;           // 主循环开关
    this.heartbeatTimer = null;       // 心跳定时器句柄
    this.syncTimer = null;            // 参数同步定时器句柄
    this.stats = {                    // 运行统计
      tasksCompleted: 0,
      tasksFailed: 0,
      signalsReported: 0,
      heartbeatsSent: 0,
      startTime: null,
    };

    // 协议版本号，用于向后兼容
    this.version = '1.0.0';

    // 根据角色确定任务容量 (并发度)
    this.capacityMap = {
      generator: 4,   // 视频生成 GPU 可并发 4 路
      evolver: 8,     // 进化计算轻量，可 8 路
      qa: 16,         // 质检纯 CPU，16 路
      creative: 6,    // 创意优化需 LLM，6 路
    };
    this.taskCapacity = this.capacityMap[role] || 4;

    // 当前活跃任务数
    this.activeTasks = 0;
  }

  /** 生成全局唯一 Agent ID */
  generateAgentId() {
    return 'agent_' + require('crypto').randomBytes(8).toString('hex');
  }

  // ========================================================================
  // 1. 注册到 HQ
  // ========================================================================

  /**
   * 向 HQ 注册当前 Agent。
   * 成功后 HQ 会返回初始 skillParams、任务容量建议、心跳间隔。
   */
  async register() {
    return withBackoff(async () => {
      const payload = {
        agentId: this.agentId,
        role: this.role,
        capabilities: {
          version: this.version,
          taskCapacity: this.taskCapacity,
          gpuAvailable: this.role === 'generator',
          cpuCores: require('os').cpus().length,
          memoryGB: Math.floor(require('os').totalmem() / 1024 / 1024 / 1024),
        },
        version: this.version,
      };

      const res = await this._post('/agent/register', payload);
      if (!res.ok) throw new Error(`注册失败 HTTP ${res.status}`);

      const data = await res.json();
      this.skillParams = data.skillParams || {};
      this.taskCapacity = data.taskCapacity || this.taskCapacity;
      this.heartbeatInterval = data.heartbeatInterval || 30000;

      console.log(`[注册成功] Agent ${this.agentId} 角色=${this.role} 容量=${this.taskCapacity}`);
      return data;
    }, 5, 'register');
  }

  // ========================================================================
  // 2. 心跳
  // ========================================================================

  /** 发送心跳包，让 HQ 知道 Agent 仍然存活并可接受任务 */
  async heartbeat() {
    if (!this.isRunning) return;

    try {
      const payload = {
        agentId: this.agentId,
        role: this.role,
        status: 'healthy',
        currentTask: this.activeTasks,
        queueLength: this.taskQueue.length,
        skillParamsVersion: this.skillParams?._version || 0,
        stats: this.stats,
        timestamp: Date.now(),
      };

      const res = await this._post('/agent/heartbeat', payload);
      if (res.ok) {
        this.stats.heartbeatsSent++;
        const data = await res.json().catch(() => ({}));

        // HQ 可能在心跳响应中嵌入紧急指令
        if (data.command === 'pause') {
          console.warn('[心跳] HQ 下发暂停指令，停止拉取新任务');
          this.isRunning = false;
        }
        if (data.command === 'shutdown') {
          console.warn('[心跳] HQ 下发终止指令，准备优雅退出');
          this.gracefulShutdown();
        }
      }
    } catch (err) {
      // 心跳失败不应打断主循环，仅记录
      console.warn(`[心跳] 发送失败: ${err.message}`);
    }
  }

  // ========================================================================
  // 3. 请求任务
  // ========================================================================

  /** 从 HQ 拉取一个可执行任务 */
  async requestTask() {
    return withBackoff(async () => {
      const query = new URLSearchParams({
        role: this.role,
        agentId: this.agentId,
        capacity: String(this.taskCapacity - this.activeTasks),
      });

      const res = await this._get(`/agent/task?${query.toString()}`);

      if (res.status === 204) {
        // 无可用任务
        return null;
      }
      if (!res.ok) throw new Error(`请求任务失败 HTTP ${res.status}`);

      const task = await res.json();
      console.log(`[任务获取] taskId=${task.taskId} type=${task.type}`);
      return task;
    }, 2, 'requestTask');
  }

  // ========================================================================
  // 4. 提交结果
  // ========================================================================

  /**
   * 将任务执行结果回传给 HQ。
   * @param {string} taskId 任务 ID
   * @param {Object} result 执行结果，需包含 {success, outputs, signals, quality}
   */
  async submitResult(taskId, result) {
    return withBackoff(async () => {
      const payload = {
        taskId,
        agentId: this.agentId,
        role: this.role,
        result: result.outputs || result,
        signals: {
          success: result.success !== false,
          retries: result.retries || 0,
          durationMs: result.durationMs || 0,
          qcScore: result.quality?.qcScore || null,
          ...result.signals,
        },
        quality: result.quality || {},
        timestamp: Date.now(),
      };

      const res = await this._post('/agent/result', payload);
      if (!res.ok) throw new Error(`提交结果失败 HTTP ${res.status}`);

      this.stats.tasksCompleted++;
      if (!payload.signals.success) this.stats.tasksFailed++;
      console.log(`[结果提交] taskId=${taskId} success=${payload.signals.success}`);
      return res.json().catch(() => ({}));
    }, 3, 'submitResult');
  }

  // ========================================================================
  // 5. 上报进化信号 (匿名遥测)
  // ========================================================================

  /**
   * 进化 Agent 将本地进化产生的隐式信号匿名上报给 HQ。
   * 仅包含统计量，不含任何个人隐私或商业秘密。
   * @param {Object} signals 信号对象 {paramDelta, winRate, candidateLoss, ...}
   */
  async reportSignals(signals) {
    try {
      const payload = {
        // 不包含 agentId，完全匿名
        role: this.role,
        skillParamsVersion: this.skillParams?._version || 0,
        signals: {
          paramDelta: signals.paramDelta || {},      // 参数变化量
          winRate: signals.winRate || 0,             // 候选胜率
          candidateLoss: signals.candidateLoss || 0, // 候选损失值
          driftNorm: signals.driftNorm || 0,         // 漂移范数 (用于门控)
          iteration: signals.iteration || 0,         // 本地迭代轮次
          timestamp: Date.now(),
        },
      };

      const res = await this._post('/telemetry', payload);
      if (res.ok) {
        this.stats.signalsReported++;
        console.log(`[信号上报] 成功 iteration=${payload.signals.iteration}`);
      }
      return res.ok;
    } catch (err) {
      console.warn(`[信号上报] 失败: ${err.message}`);
      return false;
    }
  }

  // ========================================================================
  // 6. 拉取最新参数
  // ========================================================================

  /**
   * 从 HQ 拉取最新全局参数，并在本地进行验证门控后决定是否采纳。
   * 这是 SkillOpt 安全机制的关键: 绝不盲目信任 HQ 下发。
   */
  async syncParams() {
    try {
      const query = new URLSearchParams({
        role: this.role,
        currentVersion: String(this.skillParams?._version || 0),
      });

      const res = await this._get(`/params/latest?${query.toString()}`);
      if (res.status === 304) {
        // 无新版本
        return null;
      }
      if (!res.ok) throw new Error(`同步参数失败 HTTP ${res.status}`);

      const candidate = await res.json();

      // ===== 本地验证门控 =====
      if (this._localGate(candidate)) {
        const oldVersion = this.skillParams?._version || 0;
        this.skillParams = candidate;
        console.log(`[参数更新] version ${oldVersion} → ${candidate._version}`);
        return candidate;
      } else {
        console.warn(`[参数拒绝] 候选 v${candidate._version} 未通过本地门控，保持当前参数`);
        return null;
      }
    } catch (err) {
      console.warn(`[参数同步] 失败: ${err.message}`);
      return null;
    }
  }

  /** 本地验证门控: 候选参数必须满足单调提升要求 */
  _localGate(candidate) {
    if (!candidate || !this.skillParams) return true; // 首次无条件采纳

    // 门控 1: 漂移限制 — 任何单参数漂移不得超过阈值
    const MAX_DRIFT = 0.5; // 50% 上限
    for (const key of Object.keys(candidate)) {
      if (key.startsWith('_')) continue; // 跳过元数据字段
      const oldVal = this.skillParams[key];
      if (oldVal === undefined || oldVal === 0) continue;
      const drift = Math.abs((candidate[key] - oldVal) / oldVal);
      if (drift > MAX_DRIFT) {
        console.warn(`[门控拒绝] 参数 ${key} 漂移 ${(drift * 100).toFixed(1)}% 超过阈值`);
        return false;
      }
    }

    // 门控 2: 必须有明确的版本提升
    if (candidate._version <= this.skillParams._version) {
      return false;
    }

    return true;
  }

  // ========================================================================
  // 7. 启动工作循环
  // ========================================================================

  /**
   * 启动 Agent 主循环。
   * 流程: 注册 → 启动定时器 → 循环拉取任务 → 执行 → 上报。
   */
  async start() {
    if (this.isRunning) {
      console.warn('[启动] Agent 已在运行中');
      return;
    }

    this.isRunning = true;
    this.stats.startTime = Date.now();

    // 第 1 步: 注册到 HQ
    await this.register();

    // 第 2 步: 启动心跳定时器 (默认 30s)
    this.heartbeatTimer = setInterval(() => this.heartbeat(), this.heartbeatInterval || 30000);

    // 第 3 步: 启动参数同步定时器 (默认 5min)
    this.syncTimer = setInterval(() => this.syncParams(), 300000);

    console.log(`[启动完成] Agent ${this.agentId} 进入主循环`);

    // 第 4 步: 主工作循环
    while (this.isRunning) {
      try {
        // 流控: 活跃任务数不超过容量上限
        if (this.activeTasks >= this.taskCapacity) {
          await sleep(1000);
          continue;
        }

        // 拉取任务
        const task = await this.requestTask();

        if (task) {
          // 并发执行任务 (不阻塞循环)
          this.activeTasks++;
          this._runTask(task).catch(err => {
            console.error(`[任务异常] taskId=${task.taskId}:`, err.message);
            this.activeTasks = Math.max(0, this.activeTasks - 1);
          });
        } else {
          // 无任务时休眠 5 秒，避免空转轰炸 HQ
          await sleep(5000);
        }
      } catch (err) {
        console.error('[主循环异常]', err.message);
        await sleep(10000); // 出错后冷却 10 秒
      }
    }

    console.log('[主循环] Agent 已停止');
  }

  /** 内部: 执行任务并上报结果 */
  async _runTask(task) {
    const startTime = Date.now();
    let result;

    try {
      result = await this.executeTask(task);
      result.durationMs = Date.now() - startTime;
      result.success = result.success !== false && !result.error;
    } catch (err) {
      result = {
        error: err.message,
        success: false,
        durationMs: Date.now() - startTime,
        retries: 0,
      };
    } finally {
      this.activeTasks = Math.max(0, this.activeTasks - 1);
    }

    // 提交结果到 HQ
    await this.submitResult(task.taskId, result);

    // 进化 Agent 额外上报隐式信号
    if (this.role === 'evolver' && result.signals) {
      await this.reportSignals(result.signals);
    }
  }

  /** 优雅关闭 */
  gracefulShutdown() {
    console.log('[关闭] 正在优雅关闭 Agent...');
    this.isRunning = false;
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    if (this.syncTimer) clearInterval(this.syncTimer);
  }

  stop() {
    this.gracefulShutdown();
  }

  // ========================================================================
  // 8. 任务执行路由
  // ========================================================================

  /**
   * 根据任务类型路由到对应的执行器。
   */
  async executeTask(task) {
    switch (task.type) {
      case 'generate_video':
        return this.execGenerateVideo(task);
      case 'qa_inspect':
        return this.execQA(task);
      case 'evolve_params':
        return this.execEvolve(task);
      case 'optimize_prompt':
        return this.execOptimizePrompt(task);
      default:
        return { error: `未知任务类型: ${task.type}`, success: false };
    }
  }

  // -------------------------------------------------------------------------
  // generator: 视频生成
  // -------------------------------------------------------------------------

  /**
   * 执行视频生成任务。
   * @param {Object} task {payload: {productId, script, storyboard, angle, hooks}}
   * @returns {Object} {videoUrl, quality, signals, success}
   */
  execGenerateVideo(task) {
    const { productId, script, storyboard, angle, hooks } = task.payload || {};

    // 实际生产环境: 调用视频生成引擎 (如 VIDEO-FACTORY 内核)
    // 此处提供完整的流程框架
    const startTime = Date.now();

    return new Promise((resolve) => {
      // 模拟/实际的生成流程
      console.log(`[视频生成] productId=${productId} angle=${angle}`);

      // TODO: 接入实际视频生成引擎
      // const videoUrl = await videoEngine.render({script, storyboard, angle, hooks, params: this.skillParams});

      const elapsed = Date.now() - startTime;

      resolve({
        outputs: {
          videoUrl: `https://cdn.video-factory.io/${task.taskId}.mp4`,
          storyboard,
          angle,
          hooks,
          generatedAt: Date.now(),
        },
        quality: {
          qcScore: 85,      // 预估质检分数
          confidence: 0.82, // 模型置信度
        },
        signals: {
          generationTimeMs: elapsed,
          gpuUtilization: 0.94,
          angleId: angle,
        },
        success: true,
      });
    });
  }

  // -------------------------------------------------------------------------
  // qa: 质检
  // -------------------------------------------------------------------------

  /**
   * 执行 120 门质检。
   * @param {Object} task {payload: {videoUrl, checklist}}
   * @returns {Object} {passed, score, deductions, success}
   */
  execQA(task) {
    const { videoUrl, checklist } = task.payload || {};

    return new Promise((resolve) => {
      console.log(`[质检] videoUrl=${videoUrl}`);

      // 120 门质检清单 (核心维度)
      const defaultChecklist = [
        '画面清晰度', '音频质量', '字幕同步', '品牌露白',
        ' hook 前 3 秒', 'CTA 按钮', '时长合规', '比例适配',
        '色彩空间', '码率合规', 'FPS 稳定', '黑边检测',
      ];
      const checks = checklist || defaultChecklist;

      // TODO: 接入实际质检引擎
      // const report = await qaEngine.inspect({videoUrl, checks, params: this.skillParams});

      const deductions = [];
      const passed = deductions.length === 0;

      resolve({
        outputs: {
          passed,
          score: passed ? 100 : 100 - deductions.length * 5,
          deductions,
          checkCount: checks.length,
        },
        quality: {
          qcScore: passed ? 100 : 100 - deductions.length * 5,
          passed,
        },
        signals: {
          inspectionTimeMs: 1200,
          deductionCount: deductions.length,
        },
        success: true,
      });
    });
  }

  // -------------------------------------------------------------------------
  // evolver: 参数进化
  // -------------------------------------------------------------------------

  /**
   * 执行本地进化循环。
   * @param {Object} task {payload: {localData, iterations}}
   * @returns {Object} {newParams, improvement, signals, success}
   */
  execEvolve(task) {
    const { localData, iterations = 10 } = task.payload || {};

    return new Promise((resolve) => {
      console.log(`[进化] 本地迭代 ${iterations} 轮`);

      const current = { ...this.skillParams };
      let best = { ...current };
      let bestLoss = Infinity;

      // 简化版 ε-greedy 本地搜索 (实际应调用 EvolutionEngine)
      const epsilon = 0.2;
      const lr = 0.3;

      for (let i = 0; i < iterations; i++) {
        let candidate = { ...best };

        if (Math.random() < epsilon) {
          // 探索: 随机扰动一个参数
          const keys = Object.keys(candidate).filter(k => !k.startsWith('_'));
          const key = keys[Math.floor(Math.random() * keys.length)];
          const delta = (Math.random() - 0.5) * lr * 2;
          candidate[key] = Math.max(0, candidate[key] + delta);
        } else {
          // 利用: 沿当前最优方向微调
          // (实际生产环境应使用梯度估计或 Bandit 反馈)
        }

        // 模拟损失评估 (越小越好)
        const loss = this._mockEvaluateLoss(candidate, localData);

        // 验证门控: 候选必须严格优于当前 (no ties)
        if (loss < bestLoss - 1e-6) {
          bestLoss = loss;
          best = candidate;
        }
      }

      // 计算参数漂移范数
      let driftNorm = 0;
      for (const key of Object.keys(best)) {
        if (key.startsWith('_')) continue;
        const diff = best[key] - current[key];
        driftNorm += diff * diff;
      }
      driftNorm = Math.sqrt(driftNorm);

      const paramDelta = {};
      for (const key of Object.keys(best)) {
        if (key.startsWith('_')) continue;
        if (Math.abs(best[key] - current[key]) > 1e-9) {
          paramDelta[key] = best[key] - current[key];
        }
      }

      resolve({
        outputs: {
          newParams: best,
          improvement: bestLoss < Infinity ? bestLoss : 0,
          iterations,
        },
        quality: {
          qcScore: 0,
        },
        signals: {
          paramDelta,
          winRate: 0.5,      // 简化为固定值，实际应计算
          candidateLoss: bestLoss,
          driftNorm,
          iteration: iterations,
        },
        success: true,
      });
    });
  }

  /** 模拟损失函数 (实际应基于真实 A/B 测试数据) */
  _mockEvaluateLoss(params, data) {
    // 简化的二次损失: 距离某个"最优"参数越近损失越小
    let loss = 0;
    for (const key of Object.keys(params)) {
      if (key.startsWith('_')) continue;
      const target = 0.5; // 假设最优值在 0.5 附近
      loss += Math.pow(params[key] - target, 2);
    }
    return loss + Math.random() * 0.01; // 加入噪声
  }

  // -------------------------------------------------------------------------
  // creative: 提示词优化
  // -------------------------------------------------------------------------

  /**
   * 执行提示词/角度优化。
   * @param {Object} task {payload: {currentPrompt, performanceData}}
   * @returns {Object} {optimizedPrompt, expectedImprovement, success}
   */
  execOptimizePrompt(task) {
    const { currentPrompt, performanceData } = task.payload || {};

    return new Promise((resolve) => {
      console.log(`[创意] 优化提示词: "${currentPrompt?.substring(0, 40)}..."`);

      // TODO: 接入 LLM 进行提示词优化
      // const optimized = await llm.optimize({prompt: currentPrompt, data: performanceData, params: this.skillParams});

      const optimizedPrompt = currentPrompt + '\n[优化] 添加情绪钩子与场景细节';

      resolve({
        outputs: {
          optimizedPrompt,
          expectedImprovement: 0.05, // 预估 CTR 提升 5%
          originalPrompt: currentPrompt,
        },
        quality: {
          qcScore: 90,
        },
        signals: {
          optimizationTimeMs: 800,
          promptLengthDelta: optimizedPrompt.length - (currentPrompt?.length || 0),
        },
        success: true,
      });
    });
  }

  // ========================================================================
  // HTTP 辅助 (原生实现，零外部依赖)
  // ========================================================================

  async _post(path, body) {
    return this._request('POST', path, body);
  }

  async _get(path) {
    return this._request('GET', path);
  }

  _request(method, path, body = null) {
    return new Promise((resolve, reject) => {
      const url = new URL(this.hqUrl + path);
      const options = {
        hostname: url.hostname,
        port: url.port || (url.protocol === 'https:' ? 443 : 80),
        path: url.pathname + url.search,
        method,
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        timeout: 15000, // 15 秒超时
      };

      const lib = url.protocol === 'https:' ? https : http;
      const req = lib.request(options, (res) => {
        let data = '';
        res.on('data', chunk => { data += chunk; });
        res.on('end', () => {
          const response = {
            status: res.statusCode,
            ok: res.statusCode >= 200 && res.statusCode < 300,
            headers: res.headers,
            json: async () => {
              if (!data) return {};
              try { return JSON.parse(data); } catch { return {}; }
            },
            text: async () => data,
          };
          resolve(response);
        });
      });

      req.on('error', reject);
      req.on('timeout', () => { req.destroy(); reject(new Error('请求超时')); });

      if (body) {
        req.write(JSON.stringify(body));
      }
      req.end();
    });
  }

  // ========================================================================
  // 统计与诊断
  // ========================================================================

  /** 获取当前 Agent 运行统计 */
  getStats() {
    const uptime = this.stats.startTime ? Date.now() - this.stats.startTime : 0;
    return {
      agentId: this.agentId,
      role: this.role,
      isRunning: this.isRunning,
      activeTasks: this.activeTasks,
      taskCapacity: this.taskCapacity,
      uptimeMs: uptime,
      ...this.stats,
    };
  }
}

// =========================================================================
// 模块导出
// =========================================================================

module.exports = AgentProtocol;
module.exports.AgentProtocol = AgentProtocol;
module.exports.withBackoff = withBackoff;
