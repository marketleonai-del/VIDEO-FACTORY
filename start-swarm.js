#!/usr/bin/env node
/**
 * ╔══════════════════════════════════════════════════════════════════════════╗
 * ║           VIDEO-FACTORY — 300-Agent Swarm Launcher                     ║
 * ║                                                                          ║
 * ║  Usage:                                                                  ║
 * ║    node start-swarm.js --agents 300 --mode auto --hq http://hq:8089    ║
 * ║                                                                          ║
 * ║  Role Distribution:                                                      ║
 * ║    - generator x 200 : Video generation workers                          ║
 * ║    - evolver   x 50  : Parameter evolution agents                        ║
 * ║    - qa        x 30  : Quality assurance & 120-gate inspection           ║
 * ║    - creative  x 20  : Prompt/creative optimization agents               ║
 * ║                                                                          ║
 * ║  Each Agent Lifecycle:                                                   ║
 * ║    1. Clone repo (if new) → 2. Read skill params → 3. Register w/ HQ   ║
 * ║    4. Enter work loop → 5. Report telemetry → 6. Evolve parameters     ║
 * ╚══════════════════════════════════════════════════════════════════════════╝
 */

const { fork } = require('child_process');
const path = require('path');
const os = require('os');
const fs = require('fs');

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const ROLES = {
  generator: {
    count: 200,
    script: './core/swarm/agent-worker.js',
    args: ['--role=generator'],
    description: 'Video generation — storyboard → AI video → ffmpeg stitch',
    priority: 1,
    maxMemoryMB: 2048
  },
  evolver: {
    count: 50,
    script: './core/swarm/agent-worker.js',
    args: ['--role=evolver'],
    description: 'Parameter evolution — Bandit optimization, A/B testing',
    priority: 2,
    maxMemoryMB: 512
  },
  qa: {
    count: 30,
    script: './core/swarm/agent-worker.js',
    args: ['--role=qa'],
    description: 'Quality assurance — 120-gate inspection, auto-retry',
    priority: 3,
    maxMemoryMB: 1024
  },
  creative: {
    count: 20,
    script: './core/swarm/agent-worker.js',
    args: ['--role=creative'],
    description: 'Creative optimization — hook refinement, angle scoring',
    priority: 2,
    maxMemoryMB: 1024
  }
};

const DEFAULTS = {
  agents: 300,
  hqUrl: 'http://localhost:8089',
  mode: 'auto',           // auto | manual | dry-run
  maxConcurrent: 50,      // Max simultaneous spawns
  spawnIntervalMs: 100,   // Delay between spawns
  restartDelayMs: 5000,   // Delay before auto-restart
  startTimeoutMs: 30000,  // Agent boot timeout
  heartbeatIntervalMs: 60000,
  logDir: './logs/agents'
};

// ─────────────────────────────────────────────────────────────────────────────
// Logging
// ─────────────────────────────────────────────────────────────────────────────

class Logger {
  constructor() {
    this.logDir = DEFAULTS.logDir;
    if (!fs.existsSync(this.logDir)) {
      fs.mkdirSync(this.logDir, { recursive: true });
    }
  }

  timestamp() {
    return new Date().toISOString();
  }

  write(level, message, meta = {}) {
    const line = `[${this.timestamp()}] [${level.padEnd(5)}] ${message}`;
    console.log(line);
    if (meta && Object.keys(meta).length > 0) {
      console.log('  ', JSON.stringify(meta, null, 2));
    }
    // Persist to file
    const date = new Date().toISOString().split('T')[0];
    const logFile = path.join(this.logDir, `swarm-${date}.log`);
    fs.appendFileSync(logFile, line + '\n');
  }

  info(message, meta) { this.write('INFO', message, meta); }
  warn(message, meta) { this.write('WARN', message, meta); }
  error(message, meta) { this.write('ERROR', message, meta); }
  debug(message, meta) { if (process.env.DEBUG) this.write('DEBUG', message, meta); }
}

// ─────────────────────────────────────────────────────────────────────────────
// Swarm Launcher
// ─────────────────────────────────────────────────────────────────────────────

class SwarmLauncher {
  constructor(options) {
    this.log = new Logger();
    this.totalAgents = options.agents || DEFAULTS.agents;
    this.hqUrl = options.hqUrl || DEFAULTS.hqUrl;
    this.mode = options.mode || DEFAULTS.mode;
    this.maxConcurrent = options.maxConcurrent || DEFAULTS.maxConcurrent;
    this.spawnIntervalMs = options.spawnIntervalMs || DEFAULTS.spawnIntervalMs;
    this.startTime = Date.now();

    this.agents = new Map();        // agentId → { process, role, status, startTime, lastHeartbeat }
    this.stats = {
      launched: 0,
      running: 0,
      failed: 0,
      restarted: 0,
      totalTasks: 0,
      totalVideos: 0
    };
    this.semaphore = 0;             // Current concurrent spawns
    this.shutdownRequested = false;
    this.heartbeatTimer = null;
    this.monitorTimer = null;

    // Print banner
    this.printBanner();
  }

  printBanner() {
    console.log('');
    console.log('  🐝  VIDEO-FACTORY 300-Agent Swarm Launcher');
    console.log('  ═══════════════════════════════════════════════');
    console.log(`  Total Agents : ${this.totalAgents}`);
    console.log(`  HQ Endpoint  : ${this.hqUrl}`);
    console.log(`  Mode         : ${this.mode}`);
    console.log(`  Max Concurrent: ${this.maxConcurrent}`);
    console.log(`  CPUs         : ${os.cpus().length}`);
    console.log(`  Memory       : ${Math.round(os.totalmem() / 1024 / 1024 / 1024)}GB`);
    console.log('  ═══════════════════════════════════════════════');
    console.log('');
  }

  // ── Role Distribution ────────────────────────────────────────────────────

  calculateDistribution() {
    const ratio = this.totalAgents / 300;
    const dist = {
      generator: Math.max(1, Math.round(200 * ratio)),
      evolver:   Math.max(1, Math.round(50 * ratio)),
      qa:        Math.max(1, Math.round(30 * ratio)),
      creative:  Math.max(1, Math.round(20 * ratio))
    };
    // Adjust to match total exactly
    const sum = Object.values(dist).reduce((a, b) => a + b, 0);
    if (sum !== this.totalAgents) {
      dist.generator += (this.totalAgents - sum);
    }
    return dist;
  }

  // ── HQ Registration ──────────────────────────────────────────────────────

  async registerWithHQ() {
    try {
      this.log.info(`Registering swarm with HQ: ${this.hqUrl}`);
      // Attempt HQ health check
      const { default: fetch } = await import('node-fetch');
      const res = await fetch(`${this.hqUrl}/api/health`, { timeout: 5000 });
      if (res.ok) {
        this.log.info('✅ HQ is online');
        return true;
      }
    } catch (err) {
      this.log.warn(`⚠️  HQ not reachable (${err.message}). Agents will retry registration individually.`);
    }
    return false;
  }

  // ── Launch Orchestration ─────────────────────────────────────────────────

  async launch() {
    if (this.mode === 'dry-run') {
      this.log.info('DRY-RUN MODE: No agents will be spawned');
      this.printDistribution();
      return;
    }

    // Pre-flight checks
    await this.preflightChecks();
    await this.registerWithHQ();

    const distribution = this.calculateDistribution();
    this.log.info('Agent distribution calculated:', distribution);

    // Launch by priority order
    const roleOrder = Object.entries(ROLES)
      .sort((a, b) => a[1].priority - b[1].priority)
      .map(([name]) => name);

    for (const role of roleOrder) {
      const count = distribution[role];
      if (!count) continue;
      this.log.info(`Launching ${count} ${role} agents...`);

      const promises = [];
      for (let i = 0; i < count; i++) {
        promises.push(this.launchAgentWithThrottle(role, i));
      }
      await Promise.all(promises);
    }

    this.log.info('\n✅ All agents launched!');
    this.printProgress();
    this.startMonitoring();
    this.startHeartbeat();

    // Keep process alive
    this.keepAlive();
  }

  printDistribution() {
    const dist = this.calculateDistribution();
    console.log('\n  Agent Distribution:');
    console.log('  ┌─────────────┬───────┬─────────────────────────────────────┐');
    console.log('  │ Role        │ Count │ Description                         │');
    console.log('  ├─────────────┼───────┼─────────────────────────────────────┤');
    for (const [role, count] of Object.entries(dist)) {
      const desc = ROLES[role]?.description?.substring(0, 35) || '';
      console.log(`  │ ${role.padEnd(11)} │ ${String(count).padEnd(5)} │ ${desc.padEnd(35)} │`);
    }
    console.log('  └─────────────┴───────┴─────────────────────────────────────┘');
    console.log(`  Total: ${Object.values(dist).reduce((a, b) => a + b, 0)} agents\n`);
  }

  async preflightChecks() {
    // Check available memory
    const freeMem = os.freemem();
    const requiredMem = this.totalAgents * 50 * 1024 * 1024; // ~50MB per agent estimate
    this.log.info(`Memory check: ${Math.round(freeMem / 1024 / 1024)}MB free, ~${Math.round(requiredMem / 1024 / 1024)}MB estimated needed`);

    // Check script files exist
    for (const [role, config] of Object.entries(ROLES)) {
      const scriptPath = path.resolve(config.script);
      if (!fs.existsSync(scriptPath)) {
        this.log.warn(`Script not found: ${scriptPath} (role: ${role}). Workers may fail to start.`);
      }
    }

    // Ensure log directory
    if (!fs.existsSync(DEFAULTS.logDir)) {
      fs.mkdirSync(DEFAULTS.logDir, { recursive: true });
    }
  }

  // ── Agent Lifecycle ──────────────────────────────────────────────────────

  async launchAgentWithThrottle(role, index) {
    // Throttle concurrent spawns
    while (this.semaphore >= this.maxConcurrent) {
      await this.sleep(50);
    }
    this.semaphore++;
    try {
      await this.launchAgent(role, index);
    } finally {
      this.semaphore--;
    }
  }

  launchAgent(role, index) {
    return new Promise((resolve) => {
      if (this.shutdownRequested) {
        resolve();
        return;
      }

      const agentId = `${role}_${index}_${Date.now()}_${Math.random().toString(36).substr(2, 5)}`;
      const config = ROLES[role];
      const logFile = path.join(DEFAULTS.logDir, `${agentId}.log`);
      const out = fs.openSync(logFile, 'a');

      const child = fork(config.script, [
        ...config.args,
        `--agentId=${agentId}`,
        `--hqUrl=${this.hqUrl}`,
        `--index=${index}`
      ], {
        silent: true,
        env: {
          ...process.env,
          AGENT_ID: agentId,
          AGENT_ROLE: role,
          AGENT_INDEX: String(index),
          UVG_HQ_URL: this.hqUrl
        },
        execArgv: [`--max-old-space-size=${config.maxMemoryMB}`]
      });

      // Pipe stdout/stderr to agent log file
      child.stdout?.on('data', (data) => {
        fs.writeSync(out, `[STDOUT] ${data}`);
      });
      child.stderr?.on('data', (data) => {
        fs.writeSync(out, `[STDERR] ${data}`);
      });

      // Message handling
      child.on('message', (msg) => {
        this.handleAgentMessage(agentId, role, msg);
        if (msg.type === 'ready') {
          this.agents.set(agentId, {
            process: child,
            role,
            status: 'running',
            startTime: Date.now(),
            lastHeartbeat: Date.now(),
            tasksCompleted: 0,
            videosProduced: 0
          });
          this.stats.running++;
          resolve();
        }
        if (msg.type === 'telemetry' && msg.data) {
          this.stats.totalTasks += msg.data.tasksCompleted || 0;
          this.stats.totalVideos += msg.data.videosProduced || 0;
        }
      });

      // Process exit handling
      child.on('exit', (code, signal) => {
        const info = this.agents.get(agentId);
        if (info) {
          this.stats.running--;
          this.agents.delete(agentId);
        }
        fs.closeSync(out);

        if (!this.shutdownRequested && code !== 0 && signal !== 'SIGTERM') {
          this.stats.failed++;
          this.log.warn(`Agent ${agentId} exited (code=${code}, signal=${signal}). Restarting in ${DEFAULTS.restartDelayMs}ms...`);
          setTimeout(() => {
            this.launchAgent(role, index).catch(() => {});
          }, DEFAULTS.restartDelayMs);
        }
      });

      child.on('error', (err) => {
        this.log.error(`Failed to launch agent ${agentId}:`, { error: err.message });
        this.stats.failed++;
        fs.closeSync(out);
        resolve(); // Don't block on launch failures
      });

      // Boot timeout
      setTimeout(() => {
        if (!this.agents.has(agentId) && !this.shutdownRequested) {
          this.log.warn(`Agent ${agentId} boot timeout, killing...`);
          child.kill('SIGKILL');
          // Will trigger exit handler which restarts
        }
      }, DEFAULTS.startTimeoutMs);

      this.stats.launched++;

      // Progress logging
      if (this.stats.launched % 10 === 0) {
        this.printProgress();
      }

      // Small delay to prevent spawn storm
      setTimeout(() => {}, this.spawnIntervalMs);
    });
  }

  handleAgentMessage(agentId, role, msg) {
    switch (msg.type) {
      case 'heartbeat': {
        const info = this.agents.get(agentId);
        if (info) {
          info.lastHeartbeat = Date.now();
          if (msg.tasksCompleted) info.tasksCompleted = msg.tasksCompleted;
          if (msg.videosProduced) info.videosProduced = msg.videosProduced;
        }
        break;
      }
      case 'error': {
        this.log.error(`Agent ${agentId} error:`, { error: msg.error });
        break;
      }
      case 'taskComplete': {
        this.stats.totalTasks++;
        if (msg.deliverable === 'video') this.stats.totalVideos++;
        break;
      }
      default:
        this.log.debug(`Agent ${agentId} message:`, { type: msg.type });
    }
  }

  // ── Monitoring ────────────────────────────────────────────────────────────

  startMonitoring() {
    this.log.info('Starting swarm monitor...');
    this.monitorTimer = setInterval(() => {
      this.printProgress();
      this.checkHealth();
      this.replenishIfNeeded();
    }, DEFAULTS.heartbeatIntervalMs);
  }

  startHeartbeat() {
    this.heartbeatTimer = setInterval(() => {
      this.broadcastToAgents({ type: 'heartbeat', timestamp: Date.now() });
    }, 30000);
  }

  checkHealth() {
    const now = Date.now();
    const staleThreshold = 120000; // 2 minutes
    let staleCount = 0;

    for (const [agentId, info] of this.agents) {
      const elapsed = now - info.lastHeartbeat;
      if (elapsed > staleThreshold) {
        staleCount++;
        this.log.warn(`Agent ${agentId} stale (${Math.round(elapsed / 1000)}s since last heartbeat)`);
        info.status = 'stale';
      }
    }

    if (staleCount > 0) {
      this.log.warn(`${staleCount} agents are stale`);
    }
  }

  async replenishIfNeeded() {
    const targetPercent = 0.9; // Replenish if below 90%
    if (this.stats.running >= this.totalAgents * targetPercent) return;

    this.log.info(`⚠️ Agent count low (${this.stats.running}/${this.totalAgents}). Replenishing...`);
    const distribution = this.calculateDistribution();
    const currentCounts = {};

    for (const info of this.agents.values()) {
      currentCounts[info.role] = (currentCounts[info.role] || 0) + 1;
    }

    for (const [role, target] of Object.entries(distribution)) {
      const current = currentCounts[role] || 0;
      if (current < target) {
        const needed = target - current;
        this.log.info(`Replenishing ${needed} ${role} agents...`);
        for (let i = current; i < target; i++) {
          await this.launchAgentWithThrottle(role, i);
        }
      }
    }
  }

  broadcastToAgents(message) {
    for (const [agentId, info] of this.agents) {
      try {
        info.process.send(message);
      } catch (err) {
        // Agent may have died
      }
    }
  }

  printProgress() {
    const { launched, running, failed, restarted, totalTasks, totalVideos } = this.stats;
    const total = this.totalAgents;
    const pct = total > 0 ? Math.round((running / total) * 100) : 0;
    const elapsed = ((Date.now() - this.startTime) / 1000).toFixed(1);

    // Build progress bar
    const barWidth = 30;
    const filled = Math.round((running / total) * barWidth);
    const bar = '█'.repeat(filled) + '░'.repeat(barWidth - filled);

    console.log(`  [${bar}] ${pct}% | Running: ${running}/${total} | Launched: ${launched} | Failed: ${failed} | Tasks: ${totalTasks} | Videos: ${totalVideos} | ${elapsed}s`);
  }

  // ── Shutdown ──────────────────────────────────────────────────────────────

  shutdown(signal = 'SIGTERM') {
    if (this.shutdownRequested) return;
    this.shutdownRequested = true;

    this.log.info(`\n🛑 Shutdown requested (${signal})...`);
    this.log.info(`Stopping ${this.agents.size} agents...`);

    // Stop timers
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    if (this.monitorTimer) clearInterval(this.monitorTimer);

    // Send graceful shutdown to all agents
    this.broadcastToAgents({ type: 'shutdown', reason: 'swarm-shutdown' });

    // Force kill after grace period
    setTimeout(() => {
      for (const [agentId, info] of this.agents) {
        try {
          info.process.kill('SIGKILL');
        } catch (_) {}
      }
      this.log.info('All agents force-killed');
      this.printFinalStats();
      process.exit(0);
    }, 10000);
  }

  printFinalStats() {
    const elapsed = ((Date.now() - this.startTime) / 1000).toFixed(1);
    console.log('\n  ╔═══════════════════════════════════════════╗');
    console.log('  ║         Swarm Session Summary             ║');
    console.log('  ╠═══════════════════════════════════════════╣');
    console.log(`  ║  Duration     : ${elapsed.padStart(27)}s ║`);
    console.log(`  ║  Agents Total : ${String(this.totalAgents).padStart(27)} ║`);
    console.log(`  ║  Launched     : ${String(this.stats.launched).padStart(27)} ║`);
    console.log(`  ║  Running      : ${String(this.stats.running).padStart(27)} ║`);
    console.log(`  ║  Failed       : ${String(this.stats.failed).padStart(27)} ║`);
    console.log(`  ║  Tasks        : ${String(this.stats.totalTasks).padStart(27)} ║`);
    console.log(`  ║  Videos       : ${String(this.stats.totalVideos).padStart(27)} ║`);
    console.log('  ╚═══════════════════════════════════════════╝\n');
  }

  keepAlive() {
    setInterval(() => {
      // Keep Node.js event loop alive
    }, 60000);
  }

  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// CLI Argument Parser
// ─────────────────────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const args = argv.slice(2);
  const options = {};

  for (const arg of args) {
    if (arg.startsWith('--agents=')) {
      options.agents = parseInt(arg.split('=')[1], 10);
    } else if (arg.startsWith('--hq=')) {
      options.hqUrl = arg.split('=')[1];
    } else if (arg.startsWith('--mode=')) {
      options.mode = arg.split('=')[1];
    } else if (arg.startsWith('--max-concurrent=')) {
      options.maxConcurrent = parseInt(arg.split('=')[1], 10);
    } else if (arg.startsWith('--interval=')) {
      options.spawnIntervalMs = parseInt(arg.split('=')[1], 10);
    } else if (arg === '--help' || arg === '-h') {
      printHelp();
      process.exit(0);
    } else if (arg === '--version' || arg === '-v') {
      console.log('VIDEO-FACTORY Swarm Launcher v3.0');
      process.exit(0);
    }
  }

  return options;
}

function printHelp() {
  console.log(`
VIDEO-FACTORY 300-Agent Swarm Launcher

Usage: node start-swarm.js [options]

Options:
  --agents=N          Total number of agents (default: 300)
  --hq=URL            HQ endpoint URL (default: http://localhost:8089)
  --mode=MODE         Launch mode: auto | manual | dry-run (default: auto)
  --max-concurrent=N  Max simultaneous spawns (default: 50)
  --interval=MS       Delay between spawns in ms (default: 100)
  --help, -h          Show this help message
  --version, -v       Show version

Examples:
  node start-swarm.js                              # Default 300 agents
  node start-swarm.js --agents=100                 # Small swarm
  node start-swarm.js --hq=http://hq.internal:8089 # Custom HQ
  node start-swarm.js --mode=dry-run               # Preview only
`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Entry Point
// ─────────────────────────────────────────────────────────────────────────────

const options = parseArgs(process.argv);
const launcher = new SwarmLauncher(options);

// Signal handlers
process.on('SIGINT', () => launcher.shutdown('SIGINT'));
process.on('SIGTERM', () => launcher.shutdown('SIGTERM'));
process.on('uncaughtException', (err) => {
  console.error('Uncaught exception:', err);
  launcher.shutdown('uncaughtException');
});

// Launch
launcher.launch().catch(err => {
  console.error('Swarm launch failed:', err);
  process.exit(1);
});
