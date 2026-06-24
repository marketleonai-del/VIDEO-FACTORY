/**
 * @file VersionChecker.js
 * @description 版本检查与参数同步 —— 拉取总部最优参数，本地门控后采纳
 *
 * 参数进化有两个来源：
 * 1. 本地进化：EvolutionEngine 根据隐式信号自主调优（个性化）
 * 2. 全局进化：总部聚合匿名遥测训练出全局最优参数
 *
 * VersionChecker 负责将全局参数安全同步到本地：
 * - 定期拉取远程参数中心最新版本
 * - 双条件采纳：远程质量严格优于本地，且本地样本量足够
 * - 保护本地个性化成果，全局参数劣于本地时拒绝采纳
 * - 所有操作幂等，支持失败重试和降级
 */

'use strict';

const { SkillParams } = require('./SkillParams');
const { QualitySignals } = require('./QualitySignals');

/** 管理本地参数与远程参数中心之间的同步 */
class VersionChecker {
  constructor(options) {
    this.endpoint = options.endpoint;
    this.checkIntervalMs = options.checkIntervalMs || 60 * 60 * 1000;
    this.timeoutMs = options.timeoutMs || 10000;
    this.minLocalSamples = options.minLocalSamples || 20;
    this.qualityMargin = options.qualityMargin || 0.05;
    this.lastRemoteVersion = null;
    this.lastFetchTs = null;
    this.fetchLog = [];
  }

  async fetchLatest() {
    try {
      const https = require('https');
      const url = new URL(`${this.endpoint}/params/latest`);
      const raw = await new Promise((resolve, reject) => {
        const req = https.get({
          hostname: url.hostname, port: url.port || 443,
          path: url.pathname + url.search,
          headers: { 'Accept': 'application/json', 'User-Agent': 'SkillOpt-VC/1.0' },
          timeout: this.timeoutMs,
        }, (res) => {
          if (res.statusCode !== 200) { reject(new Error(`HTTP ${res.statusCode}`)); return; }
          let data = '';
          res.on('data', c => { data += c; });
          res.on('end', () => resolve(data));
        });
        req.on('error', reject);
        req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
      });
      const parsed = JSON.parse(raw);
      if (!parsed || !parsed.params || typeof parsed.params !== 'string') {
        throw new Error('invalid response structure');
      }
      this.lastFetchTs = Date.now();
      this.lastRemoteVersion = parsed.version || null;
      this.fetchLog.push({ ts: this.lastFetchTs, version: this.lastRemoteVersion, status: 'success' });
      return {
        version: parsed.version,
        params: SkillParams.deserialize(parsed.params),
        stats: parsed.stats || null,
        publishedAt: parsed.publishedAt || null,
      };
    } catch (err) {
      this.fetchLog.push({ ts: Date.now(), status: 'error', error: err.message });
      return null;
    }
  }

  adopt(latest, localEvents) {
    if (!latest || !latest.params) {
      return { adopted: false, reason: '远程参数无效或拉取失败' };
    }
    if (!QualitySignals.hasEnoughSamples(localEvents, this.minLocalSamples)) {
      return { adopted: false, reason: `本地样本不足: ${localEvents.length} < ${this.minLocalSamples}` };
    }
    const localStats = QualitySignals.summarizeBatch(localEvents);
    let remoteMean = localStats.mean;
    if (latest.stats && typeof latest.stats.mean === 'number') remoteMean = latest.stats.mean;
    const advantage = remoteMean - localStats.mean;
    if (advantage < this.qualityMargin) {
      return {
        adopted: false,
        reason: `远程优势不足: ${advantage.toFixed(4)} < ${this.qualityMargin} ` +
                `(本地=${localStats.mean.toFixed(4)}, 远程=${remoteMean.toFixed(4)})`,
      };
    }
    return {
      adopted: true,
      reason: `采纳成功: 优势=${advantage.toFixed(4)}, 版本=${latest.version}`,
      params: latest.params,
    };
  }

  async checkAndAdopt(localEvents) {
    const latest = await this.fetchLatest();
    return this.adopt(latest, localEvents);
  }

  getStats() {
    const s = this.fetchLog.filter(f => f.status === 'success');
    return {
      totalAttempts: this.fetchLog.length,
      successes: s.length,
      errors: this.fetchLog.filter(f => f.status === 'error').length,
      lastRemoteVersion: this.lastRemoteVersion,
      lastFetchTs: this.lastFetchTs,
    };
  }
}

module.exports = { VersionChecker };
