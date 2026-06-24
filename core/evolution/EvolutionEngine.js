/**
 * @file EvolutionEngine.js
 * @description SkillOpt 进化引擎核心 —— Bandit + ε-greedy 参数自优化
 *
 * 核心机制：
 * 1. ε-greedy：以 (1-ε) 概率利用当前最优，ε 概率随机探索
 * 2. 验证门控：候选必须通过全部检查才能采纳，防止劣化参数污染线上
 * 3. 学习率融合：候选不直接替换当前，朝候选线性移动 lr 比例，防止跳变
 * 4. 一键回滚：保存 lastGood 快照，异常时秒级恢复
 */

'use strict';

const { SkillParams } = require('./SkillParams');
const { QualitySignals } = require('./QualitySignals');

/** 管理 SkillParams 的迭代进化 */
class EvolutionEngine {
  constructor(params) {
    this.params = params ? params.snapshot() : new SkillParams();
    this.history = [];
    this.negativeBuffer = [];
    this.lastGood = null;
    this.epsilon = 0.15;
    this.learningRate = 0.3;
    this.minSamples = 10;
    this.negativeRateThreshold = 0.4;
    this.weightCeiling = 5.0;
    this.weightFloor = 0.1;
  }

  /**
   * 提出候选参数 —— ε-greedy 策略核心。
   * (1-ε) 概率根据信号质量强化赢家；ε 概率随机扰动探索。
   */
  propose(signals) {
    const c = this.params.snapshot();
    const q = QualitySignals.computeQuality(signals);
    if (Math.random() < this.epsilon) {
      const keys = Object.keys(c.angleWeights);
      const t = keys[Math.floor(Math.random() * keys.length)];
      c.angleWeights[t] = this._clamp(c.angleWeights[t] * (0.8 + Math.random() * 0.4));
    } else if (q >= 0.7) {
      const winner = Object.entries(c.angleWeights).sort((a, b) => b[1] - a[1])[0][0];
      c.angleWeights[winner] = this._clamp(c.angleWeights[winner] * 1.1);
    } else if (q <= 0.3) {
      for (const k of Object.keys(c.angleWeights)) {
        const w = c.angleWeights[k];
        c.angleWeights[k] = this._clamp(w + (1.0 - w) * 0.3);
      }
      for (const k of Object.keys(c.modelBias)) c.modelBias[k] = this._clamp(c.modelBias[k] + 0.05);
    }
    return c;
  }

  /**
   * 验证门控 —— 候选必须通过全部检查才能被采纳。
   * 检查项：样本量 / 负样本率 / 参数边界 / 基线质量 / 实质性差异
   */
  validate(candidate, recentEvents) {
    if (!QualitySignals.hasEnoughSamples(recentEvents, this.minSamples)) {
      return { approved: false, reason: `样本不足: ${recentEvents.length} < ${this.minSamples}` };
    }
    const negRate = QualitySignals.negativeRate(recentEvents);
    if (negRate > this.negativeRateThreshold) {
      return { approved: false, reason: `负样本率过高: ${(negRate * 100).toFixed(1)}%` };
    }
    for (const [k, v] of Object.entries(candidate.angleWeights)) {
      if (v < this.weightFloor || v > this.weightCeiling) {
        return { approved: false, reason: `参数越界: angle.${k}=${v.toFixed(3)}` };
      }
    }
    const stats = QualitySignals.summarizeBatch(recentEvents);
    if (stats.mean <= 0.6) return { approved: false, reason: `基线质量不足: mean=${stats.mean.toFixed(3)}` };
    if (Object.keys(this.params.diff(candidate)).length === 0) {
      return { approved: false, reason: '候选与当前参数无差异' };
    }
    return { approved: true, reason: 'ok' };
  }

  /**
   * 应用学习率 —— 候选不直接替换当前，朝候选移动 lr 比例。
   * newParam = current + lr * (candidate - current)
   */
  applyLearningRate(candidate, lr) {
    const rate = lr !== undefined ? lr : this.learningRate;
    const r = this.params.snapshot();
    for (const k of Object.keys(r.angleWeights)) {
      r.angleWeights[k] = this._clamp(this.params.angleWeights[k] + rate * (candidate.angleWeights[k] - this.params.angleWeights[k]));
    }
    for (const k of Object.keys(r.modelBias)) {
      r.modelBias[k] = this._clamp(this.params.modelBias[k] + rate * (candidate.modelBias[k] - this.params.modelBias[k]));
    }
    for (const k of Object.keys(r.hookTemplates)) {
      r.hookTemplates[k] = this._clamp(this.params.hookTemplates[k] + rate * (candidate.hookTemplates[k] - this.params.hookTemplates[k]));
    }
    if (rate > 0.5 && this.params.promptSuffix !== candidate.promptSuffix) r.promptSuffix = candidate.promptSuffix;
    return r;
  }

  /** 回滚到最后一次通过验证的参数 */
  rollback() {
    if (!this.lastGood) return false;
    this.params = this.lastGood.snapshot();
    this.history.push({ ts: Date.now(), action: 'rollback', params: this.params.snapshot() });
    return true;
  }

  /**
   * 运行一轮完整进化 —— 外部唯一入口。
   * 流程：propose → validate → applyLearningRate → commit
   */
  evolveRound(signals) {
    try {
      const candidate = this.propose(signals);
      const recent = this.history.filter(h => h.signals).slice(-50).map(h => h.signals);
      const v = this.validate(candidate, recent);
      if (v.approved) {
        this.lastGood = this.params.snapshot();
        this.params = this.applyLearningRate(candidate);
        this.history.push({ ts: Date.now(), action: 'evolve', signals, params: this.params.snapshot() });
        return { success: true, params: this.params };
      }
      this.negativeBuffer.push(candidate);
      this.history.push({ ts: Date.now(), action: 'reject', signals, reason: v.reason });
      return { success: false, reason: v.reason };
    } catch (err) {
      this.history.push({ ts: Date.now(), action: 'error', error: err.message });
      return { success: false, reason: `进化异常: ${err.message}` };
    }
  }

  exportParams() { return this.params.serialize(); }
  importParams(json) { this.params = SkillParams.deserialize(json); this.lastGood = this.params.snapshot(); }

  /** 获取进化统计摘要 */
  getStats() {
    const ev = this.history.filter(h => h.action === 'evolve');
    return {
      totalRounds: this.history.length, evolves: ev.length,
      rejects: this.history.filter(h => h.action === 'reject').length,
      rollbacks: this.history.filter(h => h.action === 'rollback').length,
      negativeBufferSize: this.negativeBuffer.length,
      currentVersion: this.params.version,
      lastEvolveTs: ev.length > 0 ? ev[ev.length - 1].ts : null,
    };
  }

  /** 数值截断到 [weightFloor, weightCeiling] */
  _clamp(v) { return Math.max(this.weightFloor, Math.min(this.weightCeiling, v)); }
}

module.exports = { EvolutionEngine };
