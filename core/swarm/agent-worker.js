#!/usr/bin/env node
/**
 * Agent Worker - 300-Agent蜂群中的单个Agent
 * 
 * 启动方式:
 *   node core/swarm/agent-worker.js --role=generator --agentId=agent_001 --hqUrl=http://localhost:8089
 * 
 * 或由 start-swarm.js fork 启动:
 *   fork('./core/swarm/agent-worker.js', ['--role=generator', '--agentId=agent_001'])
 * 
 * 角色类型:
 *   - generator   : 视频生成Agent（脚本→视频）
 *   - inspector   : 质检Agent（评分+反馈）
 *   - creative    : 创意Agent（钩子+角度+矩阵）
 *   - optimizer   : 参数优化Agent（SkillOpt进化）
 *   - distributor : 分发Agent（视频上传+发布）
 */

'use strict';

const AgentProtocol = require('./agent-protocol');

// ============================================
// 命令行参数解析
// ============================================
const args = process.argv.slice(2);
const options = {};
for (const arg of args) {
  if (arg.startsWith('--')) {
    const eqIndex = arg.indexOf('=');
    if (eqIndex > 2) {
      const key = arg.slice(2, eqIndex);
      const value = arg.slice(eqIndex + 1);
      options[key] = value;
    } else {
      // 布尔型标志，如 --verbose
      options[arg.slice(2)] = true;
    }
  }
}

const role = options.role || 'generator';
const agentId = options.agentId || `agent_${process.pid}_${Date.now()}`;
const hqUrl = options.hqUrl || process.env.SWARM_HQ_URL || 'http://localhost:8089';
const verbose = options.verbose || false;

// ============================================
// Agent 启动日志
// ============================================
const startTime = Date.now();
console.log(`[${agentId}] Agent启动 | 角色: ${role} | HQ: ${hqUrl} | PID: ${process.pid}`);

// 发送 ready 信号给父进程（如果是 fork 启动的）
if (process.send) {
  process.send({ type: 'ready', agentId, role, pid: process.pid, ts: startTime });
}

// ============================================
// 创建 Agent 协议实例
// ============================================
const protocol = new AgentProtocol(hqUrl, agentId, role, { verbose });

// ============================================
// 全局错误处理
// ============================================

/** 未捕获的同步异常 */
process.on('uncaughtException', (err) => {
  console.error(`[${agentId}] 未捕获异常:`, err.message);
  console.error(err.stack);
  if (process.send) {
    process.send({ type: 'error', agentId, role, error: err.message, stack: err.stack });
  }
  // 给协议时间上报错误后退出
  setTimeout(() => process.exit(1), 1000);
});

/** 未处理的 Promise 拒绝 */
process.on('unhandledRejection', (reason, promise) => {
  console.error(`[${agentId}] 未处理Promise拒绝:`, reason);
  if (process.send) {
    process.send({ type: 'unhandledRejection', agentId, role, reason: String(reason) });
  }
});

/** 警告处理（如 DeprecationWarning） */
process.on('warning', (warning) => {
  if (verbose) {
    console.warn(`[${agentId}] Warning [${warning.name}]:`, warning.message);
  }
});

// ============================================
// 优雅退出处理
// ============================================

/** SIGTERM - 正常终止信号 */
process.on('SIGTERM', async () => {
  const uptime = Date.now() - startTime;
  console.log(`[${agentId}] 收到SIGTERM，运行时长${uptime}ms，优雅退出...`);
  
  try {
    protocol.stop();
  } catch (e) {
    console.error(`[${agentId}] 协议停止出错:`, e.message);
  }
  
  // 给 2 秒时间完成清理
  setTimeout(() => {
    console.log(`[${agentId}] 退出`);
    process.exit(0);
  }, 2000);
});

/** SIGINT - Ctrl+C */
process.on('SIGINT', async () => {
  console.log(`[${agentId}] 收到SIGINT，快速退出...`);
  try { protocol.stop(); } catch (e) { /* ignore */ }
  process.exit(0);
});

/** 父进程断开连接（PM2/cluster模式下） */
process.on('disconnect', () => {
  console.log(`[${agentId}] 与父进程断开连接，自主退出`);
  try { protocol.stop(); } catch (e) { /* ignore */ }
  process.exit(0);
});

// ============================================
// 启动工作循环
// ============================================
protocol.start().catch(err => {
  console.error(`[${agentId}] 工作循环错误:`, err.message);
  console.error(err.stack);
  if (process.send) {
    process.send({ type: 'fatal', agentId, role, error: err.message });
  }
  process.exit(1);
});
