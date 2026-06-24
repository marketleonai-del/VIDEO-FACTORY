/**
 * DataLoopback - 数据回流与闭环优化系统
 *
 * 数据流:
 *   Agent 生成视频 → 平台投放 → 用户交互产生 CTR/完播/GMV 数据
 *   → 数据回流 HQ → 校准 WinScore → 优化 SkillParams → 下发 Agent
 *
 * 核心能力:
 *   1. 接收多平台投放数据 (抖音/快手/小红书)
 *   2. 用真实数据校准预测分数 (线性回归去偏)
 *   3. Bandit 算法动态调整拍摄角度权重
 *   4. 自动生成周报 (角度表现/钩子 CTR/参数优化建议)
 */

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------

function mean(arr) {
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

function std(arr) {
  if (arr.length < 2) return 0;
  const m = mean(arr);
  return Math.sqrt(arr.reduce((s, v) => s + (v - m) * (v - m), 0) / (arr.length - 1));
}

/**
 * 简单线性回归: y = slope * x + intercept
 * @param {number[]} x 自变量数组
 * @param {number[]} y 因变量数组
 * @returns {{slope: number, intercept: number, r2: number}}
 */
function linearRegression(x, y) {
  const n = x.length;
  if (n === 0 || n !== y.length) {
    return { slope: 1, intercept: 0, r2: 0 };
  }

  const mx = mean(x);
  const my = mean(y);

  let sxy = 0; // Σ(x - mx)(y - my)
  let sxx = 0; // Σ(x - mx)²
  let syy = 0; // Σ(y - my)²

  for (let i = 0; i < n; i++) {
    const dx = x[i] - mx;
    const dy = y[i] - my;
    sxy += dx * dy;
    sxx += dx * dx;
    syy += dy * dy;
  }

  if (sxx === 0) return { slope: 1, intercept: 0, r2: 0 };

  const slope = sxy / sxx;
  const intercept = my - slope * mx;
  const r2 = syy === 0 ? 1 : (sxy * sxy) / (sxx * syy);

  return { slope, intercept, r2: Math.min(r2, 1) };
}

// ---------------------------------------------------------------------------
// DataLoopback 主类
// ---------------------------------------------------------------------------

class DataLoopback {
  constructor() {
    // 原始投放数据存储: taskId -> metrics
    this.metricsStore = new Map();

    // 校准历史 (用于追踪校准模型随时间的演变)
    this.calibrationHistory = [];

    // 当前校准模型系数
    this.currentCalibration = { slope: 1.0, intercept: 0.0, r2: 0, sampleCount: 0 };

    // 角度 Bandit 状态: angleId -> {pulls, rewards, avgReward, ucb}
    this.angleBandits = new Map();

    // 钩子 CTR 统计: hookId -> {impressions, clicks, ctr}
    this.hookStats = new Map();

    // 参数版本历史
    this.paramHistory = [];

    // 数据保留上限 (防止内存无限增长)
    this.maxStoreSize = 50000;

    // 角度探索冷却期 (避免反复尝试已确认差的角度)
    this.angleCooldown = new Map(); // angleId -> cooldownEndTimestamp
    this.COOLDOWN_MS = 24 * 3600 * 1000; // 24 小时冷却
  }

  // ========================================================================
  // 1. 接收投放数据
  // ========================================================================

  /**
   * 接收来自广告投放平台的真实效果数据。
   * @param {string} taskId  任务 ID (关联到具体视频)
   * @param {Object} metrics 投放指标
   *   - ctr:            点击率 (0~1)
   *   - completionRate: 完播率 (0~1)
   *   - gmv:            成交金额 (元)
   *   - roas:           广告支出回报率
   *   - platform:       投放平台 (douyin/kuaishou/xiaohongshu)
   *   - date:           数据日期 (ISO 字符串)
   *   - impressions:    曝光量
   *   - clicks:         点击量
   *   - videoId:        视频 ID
   *   - angleId:        拍摄角度 ID
   *   - hookId:         钩子 ID
   */
  ingestMetrics(taskId, metrics) {
    if (!taskId) {
      console.warn('[回流] 丢弃无 taskId 的数据');
      return false;
    }

    // 数据校验
    const required = ['ctr', 'completionRate', 'date'];
    for (const key of required) {
      if (metrics[key] === undefined || metrics[key] === null) {
        console.warn(`[回流] 数据缺少必填字段: ${key}`);
        return false;
      }
    }

    // 数据范围校验
    if (metrics.ctr < 0 || metrics.ctr > 1) {
      console.warn(`[回流] CTR 超出范围: ${metrics.ctr}`);
      return false;
    }
    if (metrics.completionRate < 0 || metrics.completionRate > 1) {
      console.warn(`[回流] 完播率超出范围: ${metrics.completionRate}`);
      return false;
    }

    // 写入存储 (带时间戳)
    const record = {
      taskId,
      ...metrics,
      ingestedAt: Date.now(),
    };
    this.metricsStore.set(taskId, record);

    // 更新角度 Bandit (如果有 angleId)
    if (metrics.angleId) {
      this._updateAngleBandit(metrics.angleId, metrics);
    }

    // 更新钩子 CTR 统计 (如果有 hookId)
    if (metrics.hookId && metrics.impressions && metrics.clicks !== undefined) {
      this._updateHookStats(metrics.hookId, metrics.impressions, metrics.clicks);
    }

    // 超出上限时清理最旧的数据
    if (this.metricsStore.size > this.maxStoreSize) {
      this._evictOldest(1000);
    }

    return true;
  }

  /** 批量接收投放数据 (用于定时回流) */
  ingestBatch(records) {
    let accepted = 0;
    for (const { taskId, metrics } of records) {
      if (this.ingestMetrics(taskId, metrics)) accepted++;
    }
    console.log(`[回流] 批量接收 ${records.length} 条，有效 ${accepted} 条`);
    return accepted;
  }

  // ========================================================================
  // 2. 校准 WinScore (用真实数据替代先验)
  // ========================================================================

  /**
   * 用真实投放数据校准 HQ 内部的 WinScore 预测模型。
   * 方法: 线性回归 actual = slope * predicted + intercept
   *
   * @param {Array<{taskId, predictedScore}>} predictedScores 预测分数列表
   * @returns {Object} 校准结果 {slope, intercept, r2, sampleCount, calibrationShift}
   */
  calibrateWinScore(predictedScores) {
    const x = []; // predicted
    const y = []; // actual (CTR * completionRate 作为综合指标)

    for (const { taskId, predictedScore } of predictedScores) {
      const actual = this.metricsStore.get(taskId);
      if (!actual) continue; // 尚未回流的数据跳过

      // 综合得分: CTR 权重 0.4 + 完播率权重 0.3 + 标准化 GMV 权重 0.3
      const gmvScore = actual.gmv
        ? Math.min(actual.gmv / 10000, 1) // 封顶 10000 元
        : 0;
      const compositeActual = actual.ctr * 0.4 + actual.completionRate * 0.3 + gmvScore * 0.3;

      x.push(predictedScore);
      y.push(compositeActual);
    }

    if (x.length < 10) {
      console.warn(`[校准] 样本不足 (${x.length} < 10)，跳过校准`);
      return { ...this.currentCalibration, sampleCount: x.length };
    }

    const model = linearRegression(x, y);

    // 记录校准历史
    const record = {
      timestamp: Date.now(),
      ...model,
      sampleCount: x.length,
      predMean: mean(x),
      actualMean: mean(y),
    };
    this.calibrationHistory.push(record);

    // 只保留最近 100 条历史
    if (this.calibrationHistory.length > 100) {
      this.calibrationHistory = this.calibrationHistory.slice(-100);
    }

    this.currentCalibration = {
      slope: model.slope,
      intercept: model.intercept,
      r2: model.r2,
      sampleCount: x.length,
    };

    // 校准偏移量: 预测均值与实际均值的差距
    const calibrationShift = mean(y) - mean(x);

    console.log(
      `[校准完成] 样本=${x.length} slope=${model.slope.toFixed(3)} ` +
      `intercept=${model.intercept.toFixed(4)} r²=${model.r2.toFixed(3)} ` +
      `偏移=${(calibrationShift * 100).toFixed(2)}%`
    );

    return { ...this.currentCalibration, calibrationShift };
  }

  /**
   * 使用当前校准模型修正预测分数。
   * @param {number} predicted 原始预测分数
   * @returns {number} 校准后的分数
   */
  applyCalibration(predicted) {
    const { slope, intercept } = this.currentCalibration;
    return slope * predicted + intercept;
  }

  // ========================================================================
  // 3. Bandit 角度权重更新
  // ========================================================================

  /**
   * 使用 UCB1 (Upper Confidence Bound) 算法动态调整拍摄角度权重。
   * 目标: 在"探索新角度"和"利用已知好角度"之间取得平衡。
   *
   * @param {Array<{angleId, ctr, gmv}>} angleMetrics 各角度表现数据
   */
  updateAngleWeights(angleMetrics) {
    const timestamp = Date.now();

    // 先更新 Bandit 状态
    for (const { angleId, ctr, gmv } of angleMetrics) {
      this._updateAngleBandit(angleId, { ctr, gmv });
      // 若角度表现好，解除冷却
      const bandit = this.angleBandits.get(angleId);
      if (bandit && bandit.avgReward > 0.6) {
        this.angleCooldown.delete(angleId);
      }
    }

    // 计算 UCB 分数
    const totalPulls = Array.from(this.angleBandits.values())
      .reduce((s, b) => s + b.pulls, 0);

    if (totalPulls === 0) return [];

    const results = [];
    for (const [angleId, bandit] of this.angleBandits) {
      // UCB1 = avgReward + sqrt(2 * ln(totalPulls) / pulls)
      const explorationBonus = bandit.pulls > 0
        ? Math.sqrt(2 * Math.log(totalPulls) / bandit.pulls)
        : Infinity;

      const ucbScore = bandit.avgReward + explorationBonus;

      // 检查是否在冷却期
      const cooldownEnd = this.angleCooldown.get(angleId);
      const isCooling = cooldownEnd && timestamp < cooldownEnd;

      results.push({
        angleId,
        avgReward: bandit.avgReward,
        pulls: bandit.pulls,
        explorationBonus,
        ucbScore: isCooling ? bandit.avgReward : ucbScore, // 冷却中禁止探索
        isCooling,
        recommendedWeight: this._ucbToWeight(ucbScore),
      });
    }

    // 按 UCB 分数排序
    results.sort((a, b) => b.ucbScore - a.ucbScore);

    console.log(`[角度更新] 共 ${results.length} 个角度，最优=${results[0]?.angleId}`);
    return results;
  }

  /** 内部: 更新单个角度的 Bandit 状态 */
  _updateAngleBandit(angleId, metrics) {
    if (!this.angleBandits.has(angleId)) {
      this.angleBandits.set(angleId, {
        angleId,
        pulls: 0,
        rewards: [],
        avgReward: 0,
        lastUpdated: 0,
      });
    }

    const bandit = this.angleBandits.get(angleId);

    // 将多维指标映射为单一奖励值 (CTR * 0.6 + completionRate * 0.4)
    const reward = metrics.ctr !== undefined
      ? metrics.ctr * 0.6 + (metrics.completionRate || 0) * 0.4
      : (metrics.gmv ? Math.min(metrics.gmv / 5000, 1) : 0.5);

    bandit.pulls += 1;
    bandit.rewards.push(reward);

    // 增量更新均值 (避免数组膨胀)
    bandit.avgReward += (reward - bandit.avgReward) / bandit.pulls;
    bandit.lastUpdated = Date.now();

    // 若连续 10 次奖励均低于 0.2，进入冷却期
    if (bandit.pulls >= 10 && bandit.avgReward < 0.2) {
      this.angleCooldown.set(angleId, Date.now() + this.COOLDOWN_MS);
      console.warn(`[角度冷却] ${angleId} 平均奖励过低，冷却 24h`);
    }
  }

  /** UCB 分数归一化为权重 (0.1 ~ 2.0) */
  _ucbToWeight(ucbScore) {
    // 压缩到合理范围
    return Math.max(0.1, Math.min(2.0, 0.5 + ucbScore));
  }

  /** 根据 Bandit 结果获取最优角度 (用于 HQ 任务分配) */
  getTopAngles(n = 5) {
    const entries = Array.from(this.angleBandits.entries())
      .map(([angleId, bandit]) => ({
        angleId,
        avgReward: bandit.avgReward,
        pulls: bandit.pulls,
      }))
      .sort((a, b) => b.avgReward - a.avgReward)
      .slice(0, n);

    return entries;
  }

  // ========================================================================
  // 4. 钩子 CTR 统计
  // ========================================================================

  /** 更新钩子点击率统计 */
  _updateHookStats(hookId, impressions, clicks) {
    if (!this.hookStats.has(hookId)) {
      this.hookStats.set(hookId, {
        hookId,
        totalImpressions: 0,
        totalClicks: 0,
        ctr: 0,
        updates: 0,
      });
    }

    const stat = this.hookStats.get(hookId);
    stat.totalImpressions += impressions;
    stat.totalClicks += clicks;
    stat.ctr = stat.totalClicks / stat.totalImpressions;
    stat.updates += 1;
  }

  /** 获取 CTR 最高的钩子 */
  getTopHooks(n = 10) {
    const entries = Array.from(this.hookStats.values())
      .filter(h => h.totalImpressions >= 100) // 至少 100 曝光才有统计意义
      .sort((a, b) => b.ctr - a.ctr)
      .slice(0, n);

    return entries;
  }

  // ========================================================================
  // 5. 周报生成
  // ========================================================================

  /**
   * 生成数据驱动的周报，供产品/运营团队审阅。
   * @param {Object} options 报告选项
   * @param {number} options.days 统计天数 (默认 7)
   * @returns {Object} 周报数据
   */
  generateWeeklyReport(options = {}) {
    const days = options.days || 7;
    const cutoff = Date.now() - days * 24 * 3600 * 1000;

    // 筛选最近 N 天的数据
    const recent = Array.from(this.metricsStore.values())
      .filter(m => new Date(m.date).getTime() >= cutoff);

    if (recent.length === 0) {
      return { message: '最近 7 天内无回流数据', generatedAt: new Date().toISOString() };
    }

    // ---- 5.1 整体指标 ----
    const ctrs = recent.map(m => m.ctr).filter(v => v !== undefined);
    const completions = recent.map(m => m.completionRate).filter(v => v !== undefined);
    const gmvs = recent.map(m => m.gmv).filter(v => v !== undefined);
    const roases = recent.map(m => m.roas).filter(v => v !== undefined);

    const overall = {
      videoCount: recent.length,
      avgCtr: ctrs.length ? mean(ctrs) : null,
      avgCompletionRate: completions.length ? mean(completions) : null,
      avgGmv: gmvs.length ? mean(gmvs) : null,
      avgRoas: roases.length ? mean(roases) : null,
      bestCtr: ctrs.length ? Math.max(...ctrs) : null,
      bestGmv: gmvs.length ? Math.max(...gmvs) : null,
    };

    // ---- 5.2 角度表现 TOP ----
    const topAngles = this.getTopAngles(5).map(a => ({
      angleId: a.angleId,
      avgReward: Number(a.avgReward.toFixed(4)),
      sampleSize: a.pulls,
    }));

    const worstAngles = Array.from(this.angleBandits.values())
      .filter(a => a.pulls >= 5)
      .sort((a, b) => a.avgReward - b.avgReward)
      .slice(0, 3)
      .map(a => ({
        angleId: a.angleId,
        avgReward: Number(a.avgReward.toFixed(4)),
        sampleSize: a.pulls,
      }));

    // ---- 5.3 钩子 CTR TOP ----
    const topHooks = this.getTopHooks(5).map(h => ({
      hookId: h.hookId,
      ctr: Number((h.ctr * 100).toFixed(2)) + '%',
      impressions: h.totalImpressions,
    }));

    // ---- 5.4 平台分布 ----
    const platformDist = {};
    for (const m of recent) {
      const p = m.platform || 'unknown';
      platformDist[p] = (platformDist[p] || 0) + 1;
    }

    // ---- 5.5 校准状态 ----
    const calibration = {
      ...this.currentCalibration,
      slope: Number(this.currentCalibration.slope.toFixed(3)),
      intercept: Number(this.currentCalibration.intercept.toFixed(4)),
      r2: Number(this.currentCalibration.r2.toFixed(3)),
      historyPoints: this.calibrationHistory.length,
    };

    // ---- 5.6 参数优化建议 ----
    const recommendations = this._generateRecommendations(topAngles, worstAngles, topHooks);

    const report = {
      period: `${days} 天`,
      generatedAt: new Date().toISOString(),
      overall,
      topAngles,
      worstAngles,
      topHooks,
      platformDistribution: platformDist,
      calibration,
      recommendations,
    };

    return report;
  }

  /** 生成可执行的优化建议 */
  _generateRecommendations(topAngles, worstAngles, topHooks) {
    const recs = [];

    // 建议 1: 加大好角度权重
    if (topAngles.length > 0) {
      recs.push({
        type: 'angle_weight',
        priority: 'high',
        message: `提升角度 "${topAngles[0].angleId}" 的采样权重 (平均奖励 ${topAngles[0].avgReward})`,
        action: `angleWeights.${topAngles[0].angleId} *= 1.3`,
      });
    }

    // 建议 2: 淘汰差角度
    for (const wa of worstAngles) {
      recs.push({
        type: 'angle_deprecate',
        priority: 'medium',
        message: `降低角度 "${wa.angleId}" 权重 (平均奖励 ${wa.avgReward})`,
        action: `angleWeights.${wa.angleId} *= 0.5; if < 0.1 then cooldown`,
      });
    }

    // 建议 3: 推广高 CTR 钩子
    if (topHooks.length > 0) {
      recs.push({
        type: 'hook_promote',
        priority: 'high',
        message: `优先使用钩子 "${topHooks[0].hookId}" (CTR ${topHooks[0].ctr})`,
        action: `hookWeights.${topHooks[0].hookId} *= 1.5`,
      });
    }

    // 建议 4: 校准偏移提示
    if (this.currentCalibration && Math.abs(this.currentCalibration.intercept) > 0.05) {
      recs.push({
        type: 'calibration',
        priority: 'low',
        message: `WinScore 存在系统性偏移 (intercept=${this.currentCalibration.intercept.toFixed(3)})，建议重新校准`,
        action: 'triggerCalibration()',
      });
    }

    return recs;
  }

  // ========================================================================
  // 6. 数据维护
  // ========================================================================

  /** 清理最旧的 N 条记录 */
  _evictOldest(n) {
    const entries = Array.from(this.metricsStore.entries())
      .sort((a, b) => a[1].ingestedAt - b[1].ingestedAt);

    for (let i = 0; i < Math.min(n, entries.length); i++) {
      this.metricsStore.delete(entries[i][0]);
    }
  }

  /** 导出所有数据 (用于离线分析) */
  exportMetrics() {
    return Array.from(this.metricsStore.values());
  }

  /** 获取系统健康状态 */
  getHealth() {
    return {
      metricsStoreSize: this.metricsStore.size,
      angleCount: this.angleBandits.size,
      hookCount: this.hookStats.size,
      calibrationHistorySize: this.calibrationHistory.length,
      calibration: this.currentCalibration,
      cooldownCount: this.angleCooldown.size,
    };
  }

  /** 重置所有数据 (谨慎使用) */
  reset() {
    this.metricsStore.clear();
    this.calibrationHistory = [];
    this.currentCalibration = { slope: 1.0, intercept: 0.0, r2: 0, sampleCount: 0 };
    this.angleBandits.clear();
    this.hookStats.clear();
    this.angleCooldown.clear();
    this.paramHistory = [];
    console.log('[回流] 所有数据已重置');
  }
}

// =========================================================================
// 模块导出
// =========================================================================

module.exports = { DataLoopback, linearRegression };
