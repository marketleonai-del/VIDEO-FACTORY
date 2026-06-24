/**
 * =============================================================================
 * VIDEO-FACTORY HQ - 300-Agent 蜂群协调器 (agent-coordinator.js)
 * =============================================================================
 * 
 * 架构设计:
 * - 总部(HQ)作为参数服务器，统一协调全局最优参数
 * - 每个克隆仓库 = 1个Agent节点，独立运行并上报遥测
 * - Agent通过 VersionChecker 定期拉取最新冠军参数
 * - Agent通过 Telemetry 上报隐式质量信号
 * - HQ聚合所有信号，EvolutionEngine进化参数
 * - 进化后的参数下发给所有Agent，形成闭环
 * 
 * Agent角色分配 (总计300):
 *   - generator (N=200): 视频生成Agent，执行带货视频生成任务
 *   - evolver   (N=50):  进化Agent，运行本地进化算法 + 上报信号
 *   - qa        (N=30):  质检Agent，执行120门质量检测
 *   - creative  (N=20):  创意Agent，优化提示词和拍摄角度
 * 
 * 核心机制:
 *   1. Agent注册与心跳管理
 *   2. 任务队列与负载均衡分配
 *   3. 批量任务分解（200条视频生成自动拆分为子任务）
 *   4. 结果收集与隐式信号提取
 *   5. 蜂群健康检查与故障恢复
 *   6. 参数同步与版本管理
 * 
 * 技术栈: 纯 Node.js (http/fs/crypto/events)，零运行时依赖
 * =============================================================================
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const EventEmitter = require('events');

// ============================================================================
// 配置常量
// ============================================================================

/** 协调器服务端口 */
const PORT = process.env.COORDINATOR_PORT || 7301;
/** 管理接口密钥 */
const ADMIN_KEY = process.env.HQ_ADMIN_KEY || 'dev-key-change-in-prod';
/** Agent心跳超时（毫秒）：超过此时间未收到心跳视为离线 */
const AGENT_TIMEOUT_MS = 60000;
/** 心跳检查间隔（毫秒） */
const HEALTH_CHECK_INTERVAL_MS = 15000;
/** 任务超时（毫秒） */
const TASK_TIMEOUT_MS = 300000;
/** 最大Agent数量 */
const MAX_AGENTS = 300;
/** 各角色配额 */
const ROLE_QUOTAS = {
  generator: 200,
  evolver: 50,
  qa: 30,
  creative: 20
};
/** 任务持久化路径 */
const DATA_DIR = process.env.HQ_DATA_DIR || './hq/data';
/** Agent状态文件 */
const AGENTS_FILE = path.join(DATA_DIR, 'agents.json');
/** 任务状态文件 */
const TASKS_FILE = path.join(DATA_DIR, 'tasks.json');

// ============================================================================
// 日志工具
// ============================================================================

/**
 * 协调器日志
 * @param {string} level - 日志级别 (INFO/WARN/ERROR/DEBUG)
 * @param {string} message - 日志消息
 * @param {Object} meta - 附加元数据
 */
function log(level, message, meta = {}) {
  const time = new Date().toISOString();
  const colors = { INFO: '\x1b[36m', WARN: '\x1b[33m', ERROR: '\x1b[31m', DEBUG: '\x1b[35m' };
  const reset = '\x1b[0m';
  const prefix = `${colors[level] || ''}[${time}] [COORD-${level}]${reset}`;
  console.log(`${prefix} ${message}`, meta.agentId || meta.taskId || meta.role || '');

  // 异步持久化日志
  const line = JSON.stringify({ time, level, message, ...meta }) + '\n';
  fs.appendFile(path.join(DATA_DIR, 'coordinator.log'), line, () => {});
}

/**
 * 生成唯一ID
 * @param {string} prefix - ID前缀
 * @returns {string} 唯一标识符
 */
function genId(prefix = '') {
  return prefix + crypto.randomBytes(6).toString('hex') + Date.now().toString(36);
}

// ============================================================================
// 300-Agent 蜂群协调器核心类
// ============================================================================

class AgentCoordinator extends EventEmitter {
  /**
   * 创建蜂群协调器
   * @param {Object} options - 配置选项
   */
  constructor(options = {}) {
    super();

    /** Agent注册表: agentId -> {role, status, lastPing, paramsVersion, capabilities, tasksAssigned, tasksCompleted, registeredAt} */
    this.agents = new Map();

    /** 任务队列: 待分配的任务列表 */
    this.taskQueue = [];

    /** 活跃任务映射: taskId -> {agentId, task, assignedAt, status} */
    this.activeTasks = new Map();

    /** 已完成任务存储（保留最近10000条） */
    this.completedTasks = [];

    /** 任务结果收集 */
    this.results = [];

    /** 各角色当前数量统计 */
    this.roleCounts = { generator: 0, evolver: 0, qa: 0, creative: 0 };

    /** 当前冠军参数版本 */
    this.currentVersion = 'v0-default';

    /** 健康检查定时器 */
    this.healthCheckTimer = null;

    /** 指标统计 */
    this.metrics = {
      totalAgentsRegistered: 0,
      totalTasksAssigned: 0,
      totalTasksCompleted: 0,
      totalTasksFailed: 0,
      totalSignalsExtracted: 0,
      startTime: Date.now()
    };

    log('INFO', 'AgentCoordinator 初始化完成', { maxAgents: MAX_AGENTS, quotas: ROLE_QUOTAS });
  }

  // ==========================================================================
  // Agent 生命周期管理
  // ==========================================================================

  /**
   * 注册Agent到蜂群
   * 
   * 注册流程:
   * 1. 检查角色配额是否已满
   * 2. 验证Agent基础信息
   * 3. 分配唯一agentId
   * 4. 建立心跳记录
   * 5. 触发 'agent:registered' 事件
   * 
   * @param {string} agentId - Agent唯一标识（可选，不传则自动生成）
   * @param {string} role - Agent角色: 'generator'|'evolver'|'qa'|'creative'
   * @param {Object} capabilities - Agent能力描述
   * @param {string} capabilities.model - AI模型类型 (e.g., 'kling-pro', 'lizhen')
   * @param {number} capabilities.maxConcurrent - 最大并发任务数
   * @param {string[]} capabilities.tags - 能力标签
   * @returns {{agentId: string, status: string, paramsVersion: string}} 注册结果
   */
  registerAgent(agentId, role, capabilities = {}) {
    // 校验角色
    if (!ROLE_QUOTAS[role]) {
      throw new Error(`无效角色: ${role}，有效角色: ${Object.keys(ROLE_QUOTAS).join(', ')}`);
    }

    // 检查角色配额
    if (this.roleCounts[role] >= ROLE_QUOTAS[role]) {
      throw new Error(`角色 ${role} 配额已满 (${this.roleCounts[role]}/${ROLE_QUOTAS[role]})`);
    }

    // 检查全局上限
    if (this.agents.size >= MAX_AGENTS) {
      throw new Error(`蜂群已达最大容量 ${MAX_AGENTS}`);
    }

    // 检查Agent是否已存在
    const finalAgentId = agentId || genId('agent-');
    if (this.agents.has(finalAgentId)) {
      log('WARN', `Agent ${finalAgentId} 已存在，更新信息`);
      this.roleCounts[this.agents.get(finalAgentId).role]--;
    }

    // 注册Agent
    const agentRecord = {
      agentId: finalAgentId,
      role,
      status: 'idle',        // idle | busy | offline | error
      lastPing: Date.now(),
      paramsVersion: this.currentVersion,
      capabilities: {
        model: capabilities.model || 'default',
        maxConcurrent: capabilities.maxConcurrent || 1,
        tags: capabilities.tags || [],
        ...capabilities
      },
      tasksAssigned: 0,
      tasksCompleted: 0,
      tasksFailed: 0,
      registeredAt: Date.now(),
      currentTasks: []       // 当前正在执行的任务ID列表
    };

    this.agents.set(finalAgentId, agentRecord);
    this.roleCounts[role]++;
    this.metrics.totalAgentsRegistered++;

    log('INFO', `Agent已注册`, { agentId: finalAgentId, role, totalAgents: this.agents.size });
    this.emit('agent:registered', agentRecord);

    return {
      agentId: finalAgentId,
      status: agentRecord.status,
      paramsVersion: this.currentVersion,
      roleQuota: { used: this.roleCounts[role], total: ROLE_QUOTAS[role] }
    };
  }

  /**
   * 处理Agent心跳
   * 
   * 心跳机制:
   * - Agent每15-30秒发送一次心跳
   * - HQ更新 lastPing 时间戳
   * - 如果Agent版本落后，在心跳响应中提示更新
   * 
   * @param {string} agentId - Agent标识
   * @param {Object} heartbeat - 心跳数据
   * @param {string} heartbeat.status - Agent当前状态
   * @param {string} heartbeat.paramsVersion - Agent当前参数版本
   * @param {number} heartbeat.queueDepth - Agent本地队列深度
   * @returns {{status: string, versionUpdate?: boolean, latestVersion: string}} 心跳响应
   */
  heartbeat(agentId, heartbeat = {}) {
    const agent = this.agents.get(agentId);
    if (!agent) {
      return { status: 'unknown_agent', latestVersion: this.currentVersion };
    }

    // 更新状态
    agent.lastPing = Date.now();
    if (heartbeat.status) {
      agent.status = heartbeat.status;
    }

    // 如果Agent报告空闲且当前标记为busy，检查是否有待分配任务
    if (heartbeat.status === 'idle' && agent.status === 'busy' && agent.currentTasks.length === 0) {
      agent.status = 'idle';
      this.emit('agent:available', agent);
    }

    // 检查版本是否落后
    const versionOutdated = heartbeat.paramsVersion && heartbeat.paramsVersion !== this.currentVersion;

    const response = {
      status: 'ok',
      latestVersion: this.currentVersion,
      versionUpdate: versionOutdated,
      timestamp: Date.now()
    };

    // 如果版本落后，在响应中附带最新参数摘要
    if (versionOutdated) {
      response.message = `参数有更新: ${heartbeat.paramsVersion} -> ${this.currentVersion}`;
      this.emit('agent:version-lag', { agentId, current: heartbeat.paramsVersion, expected: this.currentVersion });
    }

    return response;
  }

  /**
   * 注销Agent
   * @param {string} agentId - Agent标识
   * @param {string} [reason] - 注销原因
   */
  unregisterAgent(agentId, reason = 'manual') {
    const agent = this.agents.get(agentId);
    if (!agent) return false;

    // 将该Agent的未完成任务重新放回队列
    for (const taskId of agent.currentTasks) {
      const activeTask = this.activeTasks.get(taskId);
      if (activeTask) {
        activeTask.task.status = 'requeued';
        activeTask.retryCount = (activeTask.retryCount || 0) + 1;
        this.taskQueue.unshift(activeTask.task);
        this.activeTasks.delete(taskId);
        log('WARN', `任务 ${taskId} 因Agent注销重新入队`, { agentId, reason });
      }
    }

    this.roleCounts[agent.role]--;
    this.agents.delete(agentId);

    log('INFO', `Agent已注销`, { agentId, role: agent.role, reason, remaining: this.agents.size });
    this.emit('agent:unregistered', { agentId, role: agent.role, reason });
    return true;
  }

  // ==========================================================================
  // 任务分配系统
  // ==========================================================================

  /**
   * 创建并分配任务
   * 
   * 分配策略:
   * 1. 按角色匹配可用Agent
   * 2. 优先选择 idle 状态的Agent
   * 3. 负载均衡：选择当前任务数最少的Agent
   * 4. 返回分配结果，任务进入 activeTasks
   * 
   * @param {Object} task - 任务定义
   * @param {string} task.type - 任务类型 (generate|evolve|qa|creative)
   * @param {string} task.role - 所需角色
   * @param {Object} task.payload - 任务负载数据
   * @param {number} [task.priority=5] - 优先级 (1-10, 1最高)
   * @param {number} [task.timeout=TASK_TIMEOUT_MS] - 任务超时时间
   * @returns {{taskId: string, agentId: string, status: string}|null} 分配结果
   */
  assignTask(task) {
    const role = task.role || this._typeToRole(task.type);
    if (!role || !ROLE_QUOTAS[role]) {
      log('ERROR', `无效任务角色: ${role}`);
      return null;
    }

    // 生成任务ID
    const taskId = genId('task-');
    const enrichedTask = {
      ...task,
      taskId,
      role,
      status: 'pending',
      createdAt: Date.now(),
      priority: task.priority || 5,
      timeout: task.timeout || TASK_TIMEOUT_MS
    };

    // 寻找可用Agent（按负载均衡策略）
    const candidates = [];
    for (const [, agent] of this.agents) {
      if (agent.role === role && (agent.status === 'idle' || agent.currentTasks.length < agent.capabilities.maxConcurrent)) {
        candidates.push(agent);
      }
    }

    if (candidates.length === 0) {
      // 无可用的Agent，加入队列等待
      this.taskQueue.push(enrichedTask);
      this.taskQueue.sort((a, b) => a.priority - b.priority); // 按优先级排序
      log('DEBUG', `任务 ${taskId} 入队等待`, { role, queueLength: this.taskQueue.length });
      return { taskId, agentId: null, status: 'queued', role };
    }

    // 负载均衡：选择当前任务数最少的Agent
    candidates.sort((a, b) => a.currentTasks.length - b.currentTasks.length);
    const selectedAgent = candidates[0];

    // 分配任务
    enrichedTask.status = 'assigned';
    this.activeTasks.set(taskId, {
      agentId: selectedAgent.agentId,
      task: enrichedTask,
      assignedAt: Date.now(),
      retryCount: 0
    });

    selectedAgent.status = 'busy';
    selectedAgent.currentTasks.push(taskId);
    selectedAgent.tasksAssigned++;

    this.metrics.totalTasksAssigned++;

    log('INFO', `任务已分配`, { taskId, agentId: selectedAgent.agentId, role, type: task.type });
    this.emit('task:assigned', { taskId, agentId: selectedAgent.agentId, task: enrichedTask });

    return {
      taskId,
      agentId: selectedAgent.agentId,
      status: 'assigned',
      role
    };
  }

  /**
   * 批量生成视频任务分解
   * 
   * 分解策略（以200条视频为例）:
   * - 将200条视频拆分为200个子任务
   * - 每个子任务包含：不同角度、不同钩子文案、不同人设
   * - 任务参数包含 angle/hook/persona 组合，确保多样性
   * - 分配到200个 generator Agent 执行
   * 
   * @param {Object} product - 产品信息
   * @param {string} product.name - 产品名称
   * @param {string} product.category - 产品品类
   * @param {string} product.sellingPoints - 核心卖点
   * @param {number} [count=200] - 生成数量
   * @returns {{batchId: string, tasks: Array, estimatedTime: number}} 批次信息
   */
  batchGenerate(product, count = 200) {
    const batchId = genId('batch-');
    const tasks = [];

    // 预设多样性组合（角度 x 钩子 x 人设）
    const angles = [
      '开箱测评', '痛点解决', '场景展示', '对比实验',
      '价格解析', '使用教程', '效果见证', '成分科普',
      '穿搭搭配', '送礼推荐', '限时福利', '真实反应'
    ];
    const hooks = [
      '震惊型', '悬念型', '数字型', '反转型',
      '共鸣型', '利益型', '情感型', '权威型',
      '对比型', '挑战型', '故事型', '紧急型'
    ];
    const personas = [
      '专业测评师', '邻家姐妹', '理性消费者', '潮流达人',
      '省钱高手', '品质生活家', '新手小白', '资深用户',
      '宝妈推荐', '学生党', '职场精英', '健康生活家'
    ];

    log('INFO', `开始批量生成任务分解`, { batchId, product: product.name, count });

    for (let i = 0; i < count; i++) {
      // 为每个任务分配独特的角度+钩子+人设组合
      const angle = angles[i % angles.length];
      const hook = hooks[Math.floor(i / angles.length) % hooks.length];
      const persona = personas[Math.floor(i / (angles.length * hooks.length)) % personas.length];

      // 引入随机性：同一组合下微调参数
      const creativityBoost = 0.6 + (Math.random() * 0.3); // 0.6 - 0.9
      const pacing = 0.7 + (Math.random() * 0.25);         // 0.7 - 0.95

      const task = this.assignTask({
        type: 'generate',
        role: 'generator',
        priority: 5,
        payload: {
          batchId,
          sequence: i + 1,
          total: count,
          product: {
            name: product.name,
            category: product.category,
            sellingPoints: product.sellingPoints
          },
          config: {
            angle,
            hook,
            persona,
            creativityBoost: parseFloat(creativityBoost.toFixed(3)),
            pacing: parseFloat(pacing.toFixed(3)),
            aspectRatio: '9:16',
            resolution: '1080p',
            duration: '15-30s'
          },
          paramsVersion: this.currentVersion
        }
      });

      if (task) {
        tasks.push(task);
      }
    }

    // 计算预估时间（假设每个任务平均90秒）
    const estimatedTime = Math.ceil(count * 90 / Math.max(1, this.roleCounts.generator));

    log('INFO', `批量任务分解完成`, { batchId, assigned: tasks.filter(t => t.status === 'assigned').length, queued: tasks.filter(t => t.status === 'queued').length });

    return {
      batchId,
      tasks,
      totalTasks: count,
      assignedCount: tasks.filter(t => t.status === 'assigned').length,
      queuedCount: tasks.filter(t => t.status === 'queued').length,
      estimatedTime,
      product: product.name
    };
  }

  /**
   * 收集任务结果
   * 
   * 隐式信号提取:
   * 1. 从任务结果中提取质量指标（生成时间、重试次数等）
   * 2. 计算综合质量分数
   * 3. 构建遥测事件（通过事件发射，由collector接收）
   * 4. 释放Agent资源
   * 
   * @param {string} agentId - 执行Agent的ID
   * @param {Object} result - 任务结果
   * @param {string} result.taskId - 任务ID
   * @param {string} result.status - 执行状态 (success|failed|partial)
   * @param {Object} result.output - 输出数据
   * @param {number} result.durationMs - 执行耗时
   * @param {number} [result.qualityScore] - 显式质量评分（0-1）
   * @param {Object} [result.metrics] - 详细指标
   */
  collectResult(agentId, result) {
    const agent = this.agents.get(agentId);
    const { taskId, status, output, durationMs, qualityScore, metrics: resultMetrics } = result;

    if (!agent) {
      log('WARN', `未知Agent上报结果`, { agentId, taskId });
      return;
    }

    // 从活跃任务中移除
    const activeTask = this.activeTasks.get(taskId);
    if (activeTask) {
      this.activeTasks.delete(taskId);
    }

    // 从Agent当前任务列表中移除
    const taskIdx = agent.currentTasks.indexOf(taskId);
    if (taskIdx > -1) {
      agent.currentTasks.splice(taskIdx, 1);
    }

    // 如果Agent无其他任务，标记为idle
    if (agent.currentTasks.length === 0) {
      agent.status = 'idle';
    }

    // 隐式信号提取
    const implicitSignals = this._extractSignals(result, agent);

    // 计算综合质量分数
    const finalScore = this._calculateQualityScore(status, durationMs, qualityScore, implicitSignals, resultMetrics);

    // 构建遥测事件
    const telemetryEvent = {
      anonId: agentId,                    // 用agentId作为匿名ID
      paramsVersion: agent.paramsVersion || this.currentVersion,
      qualityScore: finalScore,
      taskType: activeTask?.task?.type || 'unknown',
      signals: implicitSignals,
      durationMs,
      timestamp: new Date().toISOString()
    };

    // 存储结果
    const completedRecord = {
      taskId,
      agentId,
      status,
      durationMs,
      qualityScore: finalScore,
      output: output ? { hasOutput: true, type: typeof output } : null,
      signals: implicitSignals,
      completedAt: Date.now()
    };

    this.completedTasks.push(completedRecord);
    this.metrics.totalTasksCompleted++;
    this.metrics.totalSignalsExtracted += Object.keys(implicitSignals).length;

    // 保留最近10000条
    if (this.completedTasks.length > 10000) {
      this.completedTasks.splice(0, this.completedTasks.length - 10000);
    }

    // 发射事件（collector.js可监听此事件接收遥测）
    this.emit('telemetry', telemetryEvent);
    this.emit('task:completed', completedRecord);

    log('INFO', `结果已收集`, { taskId, agentId, score: finalScore, duration: `${durationMs}ms`, status });

    // 尝试分配队列中的等待任务
    this._drainQueue();

    return { telemetryEvent, score: finalScore };
  }

  // ==========================================================================
  // 蜂群健康检查
  // ==========================================================================

  /**
   * 启动周期性健康检查
   * 检查内容:
   * 1. Agent心跳超时检测
   * 2. 任务超时检测与重试
   * 3. 角色配额平衡状态
   * 4. 蜂群整体健康度评分
   */
  startHealthCheck() {
    if (this.healthCheckTimer) {
      clearInterval(this.healthCheckTimer);
    }

    this.healthCheckTimer = setInterval(() => {
      this._performHealthCheck();
    }, HEALTH_CHECK_INTERVAL_MS);

    log('INFO', `健康检查已启动`, { interval: HEALTH_CHECK_INTERVAL_MS });
  }

  /**
   * 停止健康检查
   */
  stopHealthCheck() {
    if (this.healthCheckTimer) {
      clearInterval(this.healthCheckTimer);
      this.healthCheckTimer = null;
      log('INFO', '健康检查已停止');
    }
  }

  /**
   * 执行健康检查
   * @private
   */
  _performHealthCheck() {
    const now = Date.now();
    const offlineAgents = [];
    const timedOutTasks = [];

    // 1. Agent心跳超时检测
    for (const [agentId, agent] of this.agents) {
      if (now - agent.lastPing > AGENT_TIMEOUT_MS) {
        agent.status = 'offline';
        offlineAgents.push(agentId);

        // 将该Agent的任务重新分配
        for (const taskId of [...agent.currentTasks]) {
          const activeTask = this.activeTasks.get(taskId);
          if (activeTask) {
            activeTask.task.status = 'requeued';
            activeTask.retryCount = (activeTask.retryCount || 0) + 1;
            if (activeTask.retryCount <= 3) {
              this.taskQueue.unshift(activeTask.task);
            } else {
              this.metrics.totalTasksFailed++;
              log('ERROR', `任务 ${taskId} 重试次数超限，标记为失败`);
            }
            this.activeTasks.delete(taskId);
          }
        }
        agent.currentTasks = [];
      }
    }

    // 2. 任务超时检测
    for (const [taskId, activeTask] of this.activeTasks) {
      const elapsed = now - activeTask.assignedAt;
      if (elapsed > (activeTask.task.timeout || TASK_TIMEOUT_MS)) {
        timedOutTasks.push(taskId);

        // 将任务重新入队或标记失败
        activeTask.retryCount = (activeTask.retryCount || 0) + 1;
        if (activeTask.retryCount <= 3) {
          activeTask.task.status = 'requeued';
          this.taskQueue.unshift(activeTask.task);
        } else {
          this.metrics.totalTasksFailed++;
        }

        // 释放Agent
        const agent = this.agents.get(activeTask.agentId);
        if (agent) {
          const idx = agent.currentTasks.indexOf(taskId);
          if (idx > -1) agent.currentTasks.splice(idx, 1);
          if (agent.currentTasks.length === 0) agent.status = 'idle';
        }

        this.activeTasks.delete(taskId);
      }
    }

    // 3. 清理已注销的offline Agent
    for (const agentId of offlineAgents) {
      // 保留offline Agent记录用于统计，但超过10分钟未恢复则移除
      const agent = this.agents.get(agentId);
      if (agent && now - agent.lastPing > AGENT_TIMEOUT_MS * 10) {
        this.unregisterAgent(agentId, 'timeout');
      }
    }

    // 4. 蜂群健康度评分
    const health = this._calculateSwarmHealth();

    if (offlineAgents.length > 0 || timedOutTasks.length > 0) {
      log('WARN', `健康检查完成`, {
        offlineAgents: offlineAgents.length,
        timedOutTasks: timedOutTasks.length,
        healthScore: health.score,
        activeAgents: health.activeAgents,
        queueLength: this.taskQueue.length
      });
    }

    this.emit('health:check', { offlineAgents, timedOutTasks, health });
  }

  /**
   * 计算蜂群健康度
   * @private
   * @returns {{score: number, activeAgents: number, idleAgents: number, busyAgents: number, offlineAgents: number, queueDepth: number, roleBalance: Object}}
   */
  _calculateSwarmHealth() {
    let activeAgents = 0;
    let idleAgents = 0;
    let busyAgents = 0;
    let offlineAgents = 0;

    for (const [, agent] of this.agents) {
      if (agent.status === 'offline') {
        offlineAgents++;
      } else {
        activeAgents++;
        if (agent.status === 'idle') idleAgents++;
        if (agent.status === 'busy') busyAgents++;
      }
    }

    // 健康度评分 (0-100)
    const agentRatio = activeAgents / Math.max(1, this.agents.size);
    const utilization = busyAgents / Math.max(1, activeAgents);
    const queueHealth = this.taskQueue.length < 50 ? 1 : (this.taskQueue.length < 200 ? 0.7 : 0.4);
    const roleBalanceScore = this._calculateRoleBalance();

    const score = Math.round(
      (agentRatio * 30) +           // Agent在线率 30%
      (Math.min(utilization, 0.8) / 0.8 * 25) + // 利用率 25%
      (queueHealth * 25) +          // 队列健康 25%
      (roleBalanceScore * 20)       // 角色平衡 20%
    );

    return {
      score: Math.min(100, Math.max(0, score)),
      activeAgents,
      idleAgents,
      busyAgents,
      offlineAgents,
      totalAgents: this.agents.size,
      queueDepth: this.taskQueue.length,
      roleBalance: { ...this.roleCounts }
    };
  }

  /**
   * 计算角色平衡度 (0-1)
   * @private
   * @returns {number} 平衡度分数
   */
  _calculateRoleBalance() {
    let totalDiff = 0;
    for (const [role, quota] of Object.entries(ROLE_QUOTAS)) {
      const ideal = quota / MAX_AGENTS;
      const actual = this.agents.size > 0 ? this.roleCounts[role] / this.agents.size : 0;
      totalDiff += Math.abs(ideal - actual);
    }
    return Math.max(0, 1 - totalDiff / 2);
  }

  // ==========================================================================
  // 内部工具方法
  // ==========================================================================

  /**
   * 任务类型转角色
   * @private
   * @param {string} type - 任务类型
   * @returns {string|null} 角色
   */
  _typeToRole(type) {
    const mapping = {
      generate: 'generator',
      evolve: 'evolver',
      qa: 'qa',
      quality: 'qa',
      creative: 'creative',
      optimize: 'creative'
    };
    return mapping[type] || null;
  }

  /**
   * 从任务结果提取隐式信号
   * @private
   * @param {Object} result - 任务结果
   * @param {Object} agent - Agent记录
   * @returns {Object} 隐式信号
   */
  _extractSignals(result, agent) {
    const signals = {};

    // 执行速度信号（快=可能质量低，慢=可能质量高但需看具体指标）
    if (result.durationMs) {
      signals.executionSpeed = result.durationMs < 30000 ? 'fast' :
        result.durationMs < 120000 ? 'normal' : 'slow';
      signals.durationMs = result.durationMs;
    }

    // 成功状态信号
    signals.success = result.status === 'success';
    signals.retryCount = result.retryCount || 0;

    // Agent负载信号
    signals.agentLoad = agent.currentTasks.length;
    signals.agentRole = agent.role;

    // 输出质量信号（如果有详细指标）
    if (result.metrics) {
      if (typeof result.metrics.relevance === 'number') {
        signals.relevance = result.metrics.relevance;
      }
      if (typeof result.metrics.engagement === 'number') {
        signals.engagement = result.metrics.engagement;
      }
      if (typeof result.metrics.coherence === 'number') {
        signals.coherence = result.metrics.coherence;
      }
    }

    return signals;
  }

  /**
   * 计算综合质量分数 (0-1)
   * @private
   * @param {string} status - 执行状态
   * @param {number} durationMs - 执行耗时
   * @param {number} [explicitScore] - 显式评分
   * @param {Object} signals - 隐式信号
   * @param {Object} [metrics] - 详细指标
   * @returns {number} 综合质量分数
   */
  _calculateQualityScore(status, durationMs, explicitScore, signals, metrics) {
    // 基础分数
    let score = status === 'success' ? 0.7 : (status === 'partial' ? 0.4 : 0.1);

    // 显式评分权重最高
    if (typeof explicitScore === 'number') {
      score = score * 0.3 + explicitScore * 0.7;
    }

    // 执行效率调整（不是越快越好，也不是越慢越好，最优区间60-120秒）
    if (durationMs) {
      const optimalMin = 60000;
      const optimalMax = 120000;
      if (durationMs >= optimalMin && durationMs <= optimalMax) {
        score += 0.05; // 在最优区间内加分
      } else if (durationMs < 10000) {
        score -= 0.1;  // 太快可能质量有问题
      } else if (durationMs > 300000) {
        score -= 0.05; // 太慢扣分
      }
    }

    // 重试次数惩罚
    if (signals.retryCount > 0) {
      score -= Math.min(0.2, signals.retryCount * 0.05);
    }

    // 详细指标加权
    if (metrics) {
      if (typeof metrics.relevance === 'number') score += (metrics.relevance - 0.5) * 0.1;
      if (typeof metrics.engagement === 'number') score += (metrics.engagement - 0.5) * 0.1;
      if (typeof metrics.coherence === 'number') score += (metrics.coherence - 0.5) * 0.05;
    }

    return Math.max(0, Math.min(1, parseFloat(score.toFixed(4))));
  }

  /**
   * 从任务队列中分配等待的任务
   * @private
   */
  _drainQueue() {
    while (this.taskQueue.length > 0) {
      const task = this.taskQueue[0];
      const result = this.assignTask(task);
      if (result && result.status === 'assigned') {
        this.taskQueue.shift(); // 成功分配，从队列移除
      } else {
        break; // 无法分配更多，等待下次
      }
    }
  }

  // ==========================================================================
  // 状态查询与持久化
  // ==========================================================================

  /**
   * 获取蜂群状态快照
   * @returns {Object} 完整状态
   */
  getStatus() {
    const health = this._calculateSwarmHealth();
    return {
      timestamp: new Date().toISOString(),
      version: this.currentVersion,
      agents: {
        total: this.agents.size,
        byRole: { ...this.roleCounts },
        quotas: { ...ROLE_QUOTAS },
        health
      },
      tasks: {
        queueDepth: this.taskQueue.length,
        active: this.activeTasks.size,
        completed: this.completedTasks.length,
        totalAssigned: this.metrics.totalTasksAssigned,
        totalCompleted: this.metrics.totalTasksCompleted,
        totalFailed: this.metrics.totalTasksFailed
      },
      metrics: { ...this.metrics, uptime: Date.now() - this.metrics.startTime }
    };
  }

  /**
   * 持久化当前状态到磁盘
   */
  persist() {
    try {
      const agentsData = [];
      for (const [id, agent] of this.agents) {
        agentsData.push([id, { ...agent, currentTasks: [...agent.currentTasks] }]);
      }
      fs.writeFileSync(AGENTS_FILE, JSON.stringify(agentsData, null, 2));

      const tasksData = {
        queue: this.taskQueue,
        active: [...this.activeTasks.entries()],
        completed: this.completedTasks.slice(-1000) // 只保留最近1000条
      };
      fs.writeFileSync(TASKS_FILE, JSON.stringify(tasksData, null, 2));

      log('DEBUG', '状态已持久化');
    } catch (err) {
      log('ERROR', '状态持久化失败', { error: err.message });
    }
  }

  /**
   * 从磁盘恢复状态
   */
  restore() {
    try {
      if (fs.existsSync(AGENTS_FILE)) {
        const data = JSON.parse(fs.readFileSync(AGENTS_FILE, 'utf8'));
        for (const [id, agent] of data) {
          this.agents.set(id, agent);
          this.roleCounts[agent.role] = (this.roleCounts[agent.role] || 0) + 1;
        }
        log('INFO', `已恢复 ${this.agents.size} 个Agent`);
      }

      if (fs.existsSync(TASKS_FILE)) {
        const data = JSON.parse(fs.readFileSync(TASKS_FILE, 'utf8'));
        if (data.queue) this.taskQueue = data.queue;
        if (data.active) {
          for (const [taskId, taskData] of data.active) {
            this.activeTasks.set(taskId, taskData);
          }
        }
        log('INFO', `已恢复 ${this.taskQueue.length} 个排队任务, ${this.activeTasks.size} 个活跃任务`);
      }
    } catch (err) {
      log('WARN', '状态恢复失败', { error: err.message });
    }
  }

  /**
   * 更新冠军参数版本（由aggregate.js调用）
   * @param {string} version - 新版本号
   * @param {Object} params - 参数对象
   */
  updateChampion(version, params) {
    const oldVersion = this.currentVersion;
    this.currentVersion = version;

    // 通知所有Agent参数已更新
    for (const [agentId, agent] of this.agents) {
      if (agent.paramsVersion !== version && agent.status !== 'offline') {
        agent.paramsVersion = version;
        this.emit('agent:notify', { agentId, type: 'version-update', version, params });
      }
    }

    log('INFO', `冠军参数已更新`, { from: oldVersion, to: version });
    this.emit('champion:updated', { oldVersion, newVersion: version, params });
  }

  /**
   * 销毁协调器，清理资源
   */
  destroy() {
    this.stopHealthCheck();
    this.persist();
    this.agents.clear();
    this.taskQueue = [];
    this.activeTasks.clear();
    this.completedTasks = [];
    this.removeAllListeners();
    log('INFO', 'AgentCoordinator 已销毁');
  }
}

// ============================================================================
// HTTP 服务
// ============================================================================

/**
 * 创建协调器HTTP服务
 * @param {AgentCoordinator} coordinator - 协调器实例
 * @returns {http.Server} HTTP服务器
 */
function createServer(coordinator) {
  /**
   * 读取请求体
   * @param {http.IncomingMessage} req - 请求
   * @returns {Promise<Object>} JSON数据
   */
  function readBody(req) {
    return new Promise((resolve, reject) => {
      let body = '';
      req.on('data', chunk => { body += chunk; });
      req.on('end', () => {
        try { resolve(body ? JSON.parse(body) : {}); } catch (e) { reject(e); }
      });
      req.on('error', reject);
    });
  }

  /**
   * 发送JSON响应
   */
  function json(res, status, data) {
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(data));
  }

  /**
   * 鉴权检查
   */
  function isAdmin(req) {
    return req.headers['x-admin-key'] === ADMIN_KEY;
  }

  const server = http.createServer(async (req, res) => {
    // CORS
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, x-admin-key');
    if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

    const reqId = genId('req-');

    try {
      // ===== Agent注册 =====
      if (req.url === '/agent/register' && req.method === 'POST') {
        const body = await readBody(req);
        const result = coordinator.registerAgent(body.agentId, body.role, body.capabilities);
        json(res, 200, { ...result, reqId });
        return;
      }

      // ===== Agent心跳 =====
      if (req.url === '/agent/heartbeat' && req.method === 'POST') {
        const body = await readBody(req);
        const result = coordinator.heartbeat(body.agentId, body.heartbeat);
        json(res, 200, { ...result, reqId });
        return;
      }

      // ===== Agent注销 =====
      if (req.url === '/agent/unregister' && req.method === 'POST') {
        const body = await readBody(req);
        coordinator.unregisterAgent(body.agentId, body.reason);
        json(res, 200, { status: 'unregistered', agentId: body.agentId, reqId });
        return;
      }

      // ===== 任务分配 =====
      if (req.url === '/task/assign' && req.method === 'POST') {
        const body = await readBody(req);
        const result = coordinator.assignTask(body);
        if (result) {
          json(res, 200, { ...result, reqId });
        } else {
          json(res, 503, { error: 'No available agents', reqId });
        }
        return;
      }

      // ===== 批量生成 =====
      if (req.url === '/batch/generate' && req.method === 'POST') {
        const body = await readBody(req);
        const result = coordinator.batchGenerate(body.product, body.count);
        json(res, 200, { ...result, reqId });
        return;
      }

      // ===== 结果上报 =====
      if (req.url === '/task/result' && req.method === 'POST') {
        const body = await readBody(req);
        const result = coordinator.collectResult(body.agentId, body.result);
        json(res, 200, { ...result, reqId });
        return;
      }

      // ===== 蜂群状态 =====
      if (req.url === '/swarm/status' && req.method === 'GET') {
        json(res, 200, coordinator.getStatus());
        return;
      }

      // ===== 参数同步 =====
      if (req.url === '/params/sync' && req.method === 'GET') {
        json(res, 200, {
          version: coordinator.currentVersion,
          timestamp: new Date().toISOString()
        });
        return;
      }

      // ===== 更新冠军参数（admin） =====
      if (req.url === '/admin/update-champion' && req.method === 'POST') {
        if (!isAdmin(req)) { json(res, 401, { error: 'Unauthorized' }); return; }
        const body = await readBody(req);
        coordinator.updateChampion(body.version, body.params);
        json(res, 200, { status: 'updated', version: body.version, reqId });
        return;
      }

      // ===== Agent列表（admin） =====
      if (req.url === '/admin/agents' && req.method === 'GET') {
        if (!isAdmin(req)) { json(res, 401, { error: 'Unauthorized' }); return; }
        const agents = [];
        for (const [id, agent] of coordinator.agents) {
          agents.push({
            agentId: id,
            role: agent.role,
            status: agent.status,
            lastPing: agent.lastPing,
            paramsVersion: agent.paramsVersion,
            tasksAssigned: agent.tasksAssigned,
            tasksCompleted: agent.tasksCompleted,
            currentTasks: agent.currentTasks.length
          });
        }
        json(res, 200, { agents, count: agents.length, reqId });
        return;
      }

      // ===== 404 =====
      json(res, 404, { error: 'Not found', path: req.url, reqId });

    } catch (err) {
      log('ERROR', `请求处理异常`, { reqId, error: err.message, path: req.url });
      json(res, 500, { error: err.message, reqId });
    }
  });

  return server;
}

// ============================================================================
// 启动入口
// ============================================================================

function main() {
  // 确保数据目录
  if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
  }

  // 创建协调器
  const coordinator = new AgentCoordinator();
  coordinator.restore();
  coordinator.startHealthCheck();

  // 创建HTTP服务
  const server = createServer(coordinator);

  // 监听遥测事件并转发到collector
  coordinator.on('telemetry', (event) => {
    try {
      const collector = require('./collector');
      collector.telemetryStore.push(event);
    } catch (e) {
      // collector未加载，忽略
    }
  });

  // 定期持久化
  setInterval(() => coordinator.persist(), 30000);

  server.listen(PORT, () => {
    log('INFO', `VIDEO-FACTORY AgentCoordinator 已启动`, { port: PORT });
    log('INFO', `端点列表`, {
      register: `POST /agent/register`,
      heartbeat: `POST /agent/heartbeat`,
      assign: `POST /task/assign`,
      batchGenerate: `POST /batch/generate`,
      result: `POST /task/result`,
      status: `GET /swarm/status`,
      sync: `GET /params/sync`
    });
  });

  // 优雅关闭
  process.on('SIGTERM', () => {
    log('INFO', 'SIGTERM received, shutting down...');
    coordinator.destroy();
    server.close(() => process.exit(0));
  });

  process.on('SIGINT', () => {
    log('INFO', 'SIGINT received, shutting down...');
    coordinator.destroy();
    server.close(() => process.exit(0));
  });

  return { coordinator, server };
}

// 直接运行
if (require.main === module) {
  main();
}

// ============================================================================
// 模块导出
// ============================================================================

module.exports = {
  AgentCoordinator,
  createServer,
  ROLE_QUOTAS,
  MAX_AGENTS,
  main
};
