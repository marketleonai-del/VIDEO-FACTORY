/**
 * @file QualitySignals.js
 * @description 隐式质量信号采集与评分 —— 不需要用户打分的自动化质量评估
 *
 * 传统 A/B 测试依赖用户显式反馈（点赞、评分），反馈周期长、噪声大。
 * SkillOpt 改用"隐式信号"：从系统内部日志中提取成功/失败/重试/耗时等指标，
 * 通过加权公式实时计算每次生成的质量分数。
 *
 * 核心公式：
 *   quality = successRate - retryPenalty - regeneratePenalty + qcBonus * 0.3
 * 结果 clamp 到 [0, 1] 区间，可直接作为进化算法的适应度输入。
 */

'use strict';

/**
 * QualitySignals 提供静态工具方法，将原始系统事件转换为
 * 标准化的质量分数（0-1 浮点数）。
 *
 * 所有方法均为纯函数，无副作用，可安全地在任何线程中调用。
 */
class QualitySignals {
  /**
   * 根据原始信号对象计算综合质量分数。
   *
   * 输入信号字段说明：
   * - success      {boolean} 生成是否最终成功交付
   * - retries      {number}  重试次数（网络抖动、API 限流等导致）
   * - regenerated  {boolean} 是否因质量不合格而整体重生成
   * - qcScore      {number}  质检系统给出的原始分数（0-120）
   * - assembleOk   {boolean} 视频拼接/混流是否成功
   * - durationSec  {number}  生成总耗时（秒），可选
   *
   * @param {Object} signals —— 单次视频生成的全链路信号
   * @returns {number} 质量分数，范围 [0, 1]，越接近 1 越好
   */
  static computeQuality(signals) {
    if (!signals || typeof signals !== 'object') return 0;

    const successRate = signals.success === true ? 1.0 : 0.0;
    const retries = Number.isFinite(signals.retries) ? signals.retries : 0;
    const retryPenalty = Math.min(retries * 0.1, 0.5);
    const regeneratePenalty = signals.regenerated === true ? 0.3 : 0.0;
    const qcRaw = Number.isFinite(signals.qcScore) ? signals.qcScore : 0;
    const qcBonus = Math.max(0, Math.min(qcRaw, 120)) / 120;
    const assemblePenalty = signals.assembleOk === false ? 0.2 : 0.0;

    let durationPenalty = 0.0;
    if (Number.isFinite(signals.durationSec)) {
      if (signals.durationSec > 300) {
        durationPenalty = 0.2;
      } else if (signals.durationSec > 120) {
        durationPenalty = ((signals.durationSec - 120) / 180) * 0.2;
      }
    }

    const quality = successRate
      - retryPenalty
      - regeneratePenalty
      + qcBonus * 0.3
      - assemblePenalty
      - durationPenalty;

    return Math.max(0.0, Math.min(1.0, quality));
  }

  /**
   * 批量计算一组信号的质量分数，并返回统计摘要。
   * @param {Array<Object>} events —— 近期事件数组
   * @returns {Object} 统计结果，包含 mean/median/best/worst/count
   */
  static summarizeBatch(events) {
    if (!Array.isArray(events) || events.length === 0) {
      return { mean: 0, median: 0, best: 0, worst: 0, count: 0 };
    }
    const scores = events.map(e => QualitySignals.computeQuality(e));
    scores.sort((a, b) => a - b);
    const sum = scores.reduce((a, b) => a + b, 0);
    const n = scores.length;
    const mean = sum / n;
    const median = n % 2 === 1
      ? scores[Math.floor(n / 2)]
      : (scores[n / 2 - 1] + scores[n / 2]) / 2;

    return { mean, median, best: scores[n - 1], worst: scores[0], count: n };
  }

  /**
   * 判断一组信号是否达到"可进化"的最小样本量。
   * @param {Array<Object>} events —— 近期事件数组
   * @param {number} [minSamples=10] —— 最小样本阈值
   * @returns {boolean} 样本量是否足够
   */
  static hasEnoughSamples(events, minSamples = 10) {
    return Array.isArray(events) && events.length >= minSamples;
  }

  /**
   * 计算负样本率 —— 用于检测参数是否正在退化。
   * @param {Array<Object>} events —— 近期事件数组
   * @returns {number} 负样本比例 [0, 1]
   */
  static negativeRate(events) {
    if (!Array.isArray(events) || events.length === 0) return 0;
    const negatives = events.filter(e => QualitySignals.computeQuality(e) < 0.5);
    return negatives.length / events.length;
  }
}

module.exports = QualitySignals;
module.exports.QualitySignals = QualitySignals;
