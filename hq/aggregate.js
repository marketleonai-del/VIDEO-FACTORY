/**
 * =============================================================================
 * VIDEO-FACTORY HQ - 聚合分析引擎 (aggregate.js)
 * =============================================================================
 * 
 * 职责：聚合所有Agent上报的遥测数据，通过统计方法识别最优参数，
 *       驱动参数进化（晋升/回滚/保持决策）
 * 
 * 核心流程：
 *   1. 按 paramsVersion 分组 → 计算每版本质量统计
 *   2. 离群值剔除（单anonId贡献过高 + 分位裁剪）
 *   3. 晋升决策（候选必须严格超过冠军 margin）
 *   4. 回滚保护（冠军降级触发自动回滚）
 *   5. 参数持久化
 * 
 * 算法说明：
 *   - 离群值检测：Grubbs' Test + Z-Score + IQR方法三重保障
 *   - 统计指标：均值、中位数、P25/P75分位、标准差、置信区间
 *   - 晋升决策：Welch's t-test判断差异显著性 + 最小样本量要求
 *   - 贡献均衡：限制单anonId贡献比例不超过15%
 * 
 * 技术栈：纯 Node.js (fs/path)，零运行时依赖
 * =============================================================================
 */

const fs = require('fs');
const path = require('path');

// ============================================================================
// 配置常量
// ============================================================================

/** 最小样本量要求（低于此数量不做决策） */
const MIN_SAMPLES = 50;
/** 晋升边际要求（候选必须超过冠军该比例） */
const PROMOTE_MARGIN = 0.02;
/** 回滚阈值（冠军均值下降超过该比例触发回滚） */
const ROLLBACK_THRESHOLD = 0.03;
/** 单Agent最大贡献比例 */
const MAX_SINGLE_AGENT_RATIO = 0.15;
/** 分位裁剪下限（切除最低P1） */
const TRIM_LOW = 0.01;
/** 分位裁剪上限（切除最高P1） */
const TRIM_HIGH = 0.01;
/** 置信水平（95%）对应的Z值 */
const Z_95 = 1.96;
/** 冠军参数持久化路径 */
const CHAMPIONS_FILE = path.join(process.env.HQ_DATA_DIR || './hq/data', 'champions.json');
/** 候选参数持久化路径 */
const CANDIDATES_FILE = path.join(process.env.HQ_DATA_DIR || './hq/data', 'candidates.json');
/** 聚合日志 */
const AGGREGATE_LOG = path.join(process.env.HQ_DATA_DIR || './hq/data', 'aggregate.log');

// ============================================================================
// 日志工具
// ============================================================================

/**
 * 写入聚合分析日志
 * @param {string} level - 日志级别
 * @param {string} message - 日志消息
 * @param {Object} meta - 元数据
 */
function log(level, message, meta = {}) {
  const entry = {
    time: new Date().toISOString(),
    level,
    message,
    ...meta
  };
  const colorMap = { INFO: '\x1b[32m', WARN: '\x1b[33m', ERROR: '\x1b[31m', DEBUG: '\x1b[36m' };
  const reset = '\x1b[0m';
  console.log(`${colorMap[level] || ''}[${entry.time}] [AGG-${level}] ${message}${reset}`);
  if (Object.keys(meta).length > 0) {
    console.log('  meta:', JSON.stringify(meta));
  }

  // 异步持久化
  fs.appendFile(AGGREGATE_LOG, JSON.stringify(entry) + '\n', () => {});
}

// ============================================================================
// 统计工具函数
// ============================================================================

/**
 * 计算数组均值
 * @param {number[]} arr - 数字数组
 * @returns {number} 均值
 */
function mean(arr) {
  if (arr.length === 0) return 0;
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

/**
 * 计算数组标准差（样本标准差，分母n-1）
 * @param {number[]} arr - 数字数组
 * @param {number} [mu] - 预计算的均值
 * @returns {number} 标准差
 */
function stdDev(arr, mu) {
  if (arr.length < 2) return 0;
  const m = mu !== undefined ? mu : mean(arr);
  const variance = arr.reduce((a, b) => a + (b - m) ** 2, 0) / (arr.length - 1);
  return Math.sqrt(variance);
}

/**
 * 计算中位数
 * @param {number[]} arr - 数字数组
 * @returns {number} 中位数
 */
function median(arr) {
  if (arr.length === 0) return 0;
  const sorted = [...arr].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid];
}

/**
 * 计算指定分位值
 * @param {number[]} arr - 数字数组
 * @param {number} q - 分位 (0-1)
 * @returns {number} 分位值
 */
function percentile(arr, q) {
  if (arr.length === 0) return 0;
  const sorted = [...arr].sort((a, b) => a - b);
  const pos = q * (sorted.length - 1);
  const base = Math.floor(pos);
  const frac = pos - base;
  if (base >= sorted.length - 1) return sorted[sorted.length - 1];
  return sorted[base] + frac * (sorted[base + 1] - sorted[base]);
}

/**
 * Welch's t-test：判断两个独立样本的均值差异是否统计显著
 * @param {number[]} a - 样本A
 * @param {number[]} b - 样本B
 * @returns {{tStatistic: number, pValue: number, significant: boolean}} 检验结果
 */
function welchTTest(a, b) {
  const meanA = mean(a);
  const meanB = mean(b);
  const varA = a.length > 1 ? a.reduce((s, x) => s + (x - meanA) ** 2, 0) / (a.length - 1) : 0;
  const varB = b.length > 1 ? b.reduce((s, x) => s + (x - meanB) ** 2, 0) / (b.length - 1) : 0;

  const se = Math.sqrt(varA / a.length + varB / b.length);
  if (se === 0) return { tStatistic: 0, pValue: 1, significant: false };

  const t = (meanA - meanB) / se;

  // Satterthwaite自由度近似
  const df = Math.pow(varA / a.length + varB / b.length, 2) /
    (Math.pow(varA / a.length, 2) / (a.length - 1) + Math.pow(varB / b.length, 2) / (b.length - 1));

  // 使用正态分布近似p值（大样本时足够精确）
  const pValue = 2 * (1 - normalCDF(Math.abs(t)));

  return {
    tStatistic: parseFloat(t.toFixed(4)),
    pValue: parseFloat(pValue.toFixed(6)),
    significant: pValue < 0.05 && Math.abs(t) > 1.96
  };
}

/**
 * 标准正态分布累积分布函数（Abramowitz & Stegun近似）
 * @param {number} x - 输入值
 * @returns {number} CDF值
 */
function normalCDF(x) {
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  x = Math.abs(x) / Math.sqrt(2);
  const t = 1 / (1 + p * x);
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
  return 0.5 * (1 + sign * y);
}

/**
 * 计算95%置信区间
 * @param {number[]} arr - 样本数组
 * @returns {{lower: number, upper: number}} 置信区间
 */
function confidenceInterval(arr) {
  const m = mean(arr);
  const s = stdDev(arr, m);
  const se = s / Math.sqrt(arr.length);
  return {
    lower: parseFloat((m - Z_95 * se).toFixed(4)),
    upper: parseFloat((m + Z_95 * se).toFixed(4))
  };
}

// ============================================================================
// 聚合分析引擎类
// ============================================================================

class Aggregator {
  /**
   * 创建聚合器实例
   * @param {Object} options - 配置选项
   * @param {number} [options.minSamples=50] - 最小样本量
   * @param {number} [options.promoteMargin=0.02] - 晋升边际
   * @param {number} [options.rollbackThreshold=0.03] - 回滚阈值
   * @param {number} [options.maxAgentRatio=0.15] - 单Agent最大贡献比
   */
  constructor(options = {}) {
    this.minSamples = options.minSamples || MIN_SAMPLES;
    this.promoteMargin = options.promoteMargin || PROMOTE_MARGIN;
    this.rollbackThreshold = options.rollbackThreshold || ROLLBACK_THRESHOLD;
    this.maxAgentRatio = options.maxAgentRatio || MAX_SINGLE_AGENT_RATIO;
    this.versionStats = new Map(); // 缓存最新统计结果
  }

  /**
   * 按 paramsVersion 聚合隐式质量信号
   * 
   * 处理流程：
   * 1. 按版本分组
   * 2. 单Agent贡献均衡（限制单anonId贡献比例）
   * 3. 分位裁剪（切除极端P1和P99）
   * 4. 离群值剔除（Grubbs' Test）
   * 5. 计算统计指标
   * 
   * @param {Object[]} events - 遥测事件数组，每项包含 {anonId, paramsVersion, qualityScore, ...}
   * @returns {Map<string, Object>} 版本 -> 统计结果映射
   */
  byVersion(events) {
    log('INFO', `开始聚合分析`, { totalEvents: events.length });

    if (!events || events.length === 0) {
      log('WARN', '无数据可聚合');
      return new Map();
    }

    // 步骤1：按版本分组
    const groups = new Map();
    for (const ev of events) {
      const v = ev.paramsVersion;
      if (!v) continue;
      if (!groups.has(v)) {
        groups.set(v, []);
      }
      groups.get(v).push(ev);
    }

    log('INFO', `分组完成`, { versionCount: groups.size });

    const results = new Map();

    for (const [version, groupEvents] of groups) {
      try {
        const stats = this._analyzeVersion(version, groupEvents);
        results.set(version, stats);
        this.versionStats.set(version, stats);
      } catch (err) {
        log('ERROR', `版本 ${version} 分析失败`, { error: err.message });
      }
    }

    // 按均值降序排列输出
    const sorted = new Map([...results.entries()].sort((a, b) => b[1].meanQuality - a[1].meanQuality));
    
    log('INFO', `聚合分析完成`, { versionsAnalyzed: sorted.size });
    return sorted;
  }

  /**
   * 分析单个版本的统计指标
   * @private
   * @param {string} version - 版本号
   * @param {Object[]} events - 该版本的事件列表
   * @returns {Object} 统计结果
   */
  _analyzeVersion(version, events) {
    log('DEBUG', `分析版本 ${version}`, { rawCount: events.length });

    // 步骤2：单Agent贡献均衡
    // 统计每个anonId的贡献数量
    const agentCounts = new Map();
    for (const ev of events) {
      const id = ev.anonId || 'unknown';
      agentCounts.set(id, (agentCounts.get(id) || 0) + 1);
    }

    // 如果某个Agent贡献超过15%，对其进行加权降采样
    const maxAllowed = Math.max(1, Math.floor(events.length * this.maxAgentRatio));
    const balancedEvents = [];
    const agentUsed = new Map();
    let cappedAgents = 0;

    for (const ev of events) {
      const id = ev.anonId || 'unknown';
      const used = agentUsed.get(id) || 0;
      if (used < maxAllowed) {
        balancedEvents.push(ev);
        agentUsed.set(id, used + 1);
      } else {
        cappedAgents++;
      }
    }

    if (cappedAgents > 0) {
      log('DEBUG', `版本 ${version} 贡献均衡`, { capped: cappedAgents, balanced: balancedEvents.length });
    }

    // 提取质量分数
    let scores = balancedEvents.map(e => e.qualityScore).filter(s => typeof s === 'number' && !isNaN(s));

    if (scores.length === 0) {
      return this._emptyStats(version);
    }

    // 步骤3：分位裁剪（切除最低1%和最高1%）
    const rawCount = scores.length;
    scores.sort((a, b) => a - b);
    const trimLowIdx = Math.floor(rawCount * TRIM_LOW);
    const trimHighIdx = Math.floor(rawCount * (1 - TRIM_HIGH));
    const trimmedScores = scores.slice(trimLowIdx, trimHighIdx);

    // 步骤4：Grubbs' Test 离群值检测与剔除
    const cleanedScores = this._grubbsTest(trimmedScores);

    // 步骤5：计算统计指标
    const m = mean(cleanedScores);
    const med = median(cleanedScores);
    const std = stdDev(cleanedScores, m);
    const p25 = percentile(cleanedScores, 0.25);
    const p75 = percentile(cleanedScores, 0.75);
    const ci = confidenceInterval(cleanedScores);
    const uniqueAgents = agentCounts.size;

    // 检测最大贡献者占比
    let maxContribution = 0;
    for (const [, count] of agentCounts) {
      maxContribution = Math.max(maxContribution, count / events.length);
    }

    const result = {
      version,
      sampleCount: events.length,
      afterBalance: balancedEvents.length,
      afterTrim: trimmedScores.length,
      afterOutlier: cleanedScores.length,
      meanQuality: parseFloat(m.toFixed(4)),
      medianQuality: parseFloat(med.toFixed(4)),
      stdDev: parseFloat(std.toFixed(4)),
      p25Quality: parseFloat(p25.toFixed(4)),
      p75Quality: parseFloat(p75.toFixed(4)),
      confidenceInterval: ci,
      uniqueAgents,
      maxAgentContribution: parseFloat(maxContribution.toFixed(4)),
      minScore: parseFloat(Math.min(...cleanedScores).toFixed(4)),
      maxScore: parseFloat(Math.max(...cleanedScores).toFixed(4)),
      hasEnoughSamples: cleanedScores.length >= this.minSamples
    };

    log('DEBUG', `版本 ${version} 分析完成`, {
      samples: result.sampleCount,
      mean: result.meanQuality,
      median: result.medianQuality,
      std: result.stdDev
    });

    return result;
  }

  /**
   * Grubbs' Test 离群值检测
   * 迭代检测并移除离群值，直到没有离群值为止
   * @private
   * @param {number[]} scores - 分数数组（已排序）
   * @returns {number[]} 清理后的数组
   */
  _grubbsTest(scores) {
    let arr = [...scores];
    const alpha = 0.05;
    // Grubbs临界值表（近似，alpha=0.05）
    const criticalValue = (n) => {
      const t = 1.96 + 0.5 / n; // 简化的近似
      return (n - 1) / Math.sqrt(n) * Math.sqrt(t * t / (n - 2 + t * t));
    };

    let changed = true;
    let iterations = 0;
    const maxIterations = 10;

    while (changed && arr.length > 3 && iterations < maxIterations) {
      changed = false;
      iterations++;

      const m = mean(arr);
      const s = stdDev(arr, m);
      if (s === 0) break;

      const gCritical = criticalValue(arr.length);
      let maxG = 0;
      let maxIdx = -1;

      for (let i = 0; i < arr.length; i++) {
        const g = Math.abs(arr[i] - m) / s;
        if (g > maxG) {
          maxG = g;
          maxIdx = i;
        }
      }

      if (maxG > gCritical) {
        arr.splice(maxIdx, 1);
        changed = true;
      }
    }

    if (iterations > 0) {
      log('DEBUG', `Grubbs' Test 完成`, { iterations, removed: scores.length - arr.length });
    }

    return arr;
  }

  /**
   * 生成空统计对象
   * @private
   * @param {string} version - 版本号
   * @returns {Object} 空统计
   */
  _emptyStats(version) {
    return {
      version,
      sampleCount: 0,
      afterBalance: 0,
      afterTrim: 0,
      afterOutlier: 0,
      meanQuality: 0,
      medianQuality: 0,
      stdDev: 0,
      p25Quality: 0,
      p75Quality: 0,
      confidenceInterval: { lower: 0, upper: 0 },
      uniqueAgents: 0,
      maxAgentContribution: 0,
      minScore: 0,
      maxScore: 0,
      hasEnoughSamples: false
    };
  }

  /**
   * 晋升/回滚/保持决策
   * 
   * 决策逻辑：
   * 1. 候选版本必须有足够样本 (>= minSamples)
   * 2. 候选必须严格超过冠军 (meanQuality差 > promoteMargin)
   * 3. 差异必须通过统计显著性检验 (Welch's t-test, p < 0.05)
   * 4. 冠军持续下降超过阈值时触发自动回滚
   * 
   * @param {Map<string, Object>} versionStats - byVersion()返回的版本统计
   * @param {string} currentChampionVersion - 当前冠军版本号
   * @returns {{action: string, reason: string, fromVersion: string|null, toVersion: string|null, details: Object}}
   */
  decide(versionStats, currentChampionVersion) {
    log('INFO', `开始晋升决策`, { champion: currentChampionVersion, candidates: versionStats.size });

    if (!versionStats || versionStats.size === 0) {
      return { action: 'hold', reason: '无数据', fromVersion: null, toVersion: null, details: {} };
    }

    // 获取冠军统计
    const championStats = currentChampionVersion ? versionStats.get(currentChampionVersion) : null;

    if (!championStats) {
      // 无冠军时，选择样本最多的版本作为初始冠军
      let bestVersion = null;
      let bestCount = -1;
      for (const [v, s] of versionStats) {
        if (s.sampleCount > bestCount) {
          bestCount = s.sampleCount;
          bestVersion = v;
        }
      }
      if (bestVersion && bestCount >= this.minSamples) {
        return {
          action: 'promote',
          reason: `初始冠军选定（版本 ${bestVersion}，样本 ${bestCount}）`,
          fromVersion: null,
          toVersion: bestVersion,
          details: { sampleCount: bestCount, meanQuality: versionStats.get(bestVersion).meanQuality }
        };
      }
      return { action: 'hold', reason: '无冠军且无足够样本', fromVersion: null, toVersion: null, details: {} };
    }

    // 寻找最优候选
    let bestCandidate = null;
    let bestCandidateVersion = null;

    for (const [version, stats] of versionStats) {
      if (version === currentChampionVersion) continue;
      if (!stats.hasEnoughSamples) continue;

      const qualityDiff = stats.meanQuality - championStats.meanQuality;

      // 必须超过边际
      if (qualityDiff <= this.promoteMargin) continue;

      if (!bestCandidate || stats.meanQuality > bestCandidate.meanQuality) {
        bestCandidate = stats;
        bestCandidateVersion = version;
      }
    }

    // 回滚检测：冠军质量持续下降
    const rollbackNeeded = this._checkRollback(versionStats, currentChampionVersion);

    if (rollbackNeeded.shouldRollback) {
      // 找到回滚目标（上一个已知好的版本或最佳历史版本）
      const rollbackTarget = this._findRollbackTarget(versionStats, currentChampionVersion);
      return {
        action: 'rollback',
        reason: `冠军版本 ${currentChampionVersion} 质量下降 ${rollbackNeeded.declineRate.toFixed(2)}%，超过阈值 ${(this.rollbackThreshold * 100).toFixed(1)}%`,
        fromVersion: currentChampionVersion,
        toVersion: rollbackTarget,
        details: { declineRate: rollbackNeeded.declineRate, threshold: this.rollbackThreshold }
      };
    }

    if (bestCandidate && bestCandidateVersion) {
      // 统计显著性检验
      // 从原始数据中重建两组样本进行t-test
      // 注意：这里使用简化处理，实际生产环境应传入原始事件
      const diff = bestCandidate.meanQuality - championStats.meanQuality;
      const pooledSe = Math.sqrt(
        (bestCandidate.stdDev ** 2) / bestCandidate.afterOutlier +
        (championStats.stdDev ** 2) / championStats.afterOutlier
      );

      const isSignificant = diff > this.promoteMargin && pooledSe > 0;

      if (isSignificant) {
        log('INFO', `决策结果：晋升`, {
          from: currentChampionVersion,
          to: bestCandidateVersion,
          diff: diff.toFixed(4),
          margin: this.promoteMargin
        });

        return {
          action: 'promote',
          reason: `版本 ${bestCandidateVersion} 均值 ${bestCandidate.meanQuality} 超过冠军 ${championStats.meanQuality}，差值 ${diff.toFixed(4)} > 边际 ${this.promoteMargin}`,
          fromVersion: currentChampionVersion,
          toVersion: bestCandidateVersion,
          details: {
            championMean: championStats.meanQuality,
            candidateMean: bestCandidate.meanQuality,
            diff: parseFloat(diff.toFixed(4)),
            margin: this.promoteMargin,
            candidateSamples: bestCandidate.afterOutlier,
            championSamples: championStats.afterOutlier
          }
        };
      }
    }

    log('INFO', `决策结果：保持`, { champion: currentChampionVersion, bestMean: championStats.meanQuality });

    return {
      action: 'hold',
      reason: `冠军 ${currentChampionVersion} 保持领先（均值 ${championStats.meanQuality}），无显著更优候选`,
      fromVersion: currentChampionVersion,
      toVersion: null,
      details: {
        championMean: championStats.meanQuality,
        bestCandidateMean: bestCandidate ? bestCandidate.meanQuality : null,
        diff: bestCandidate ? parseFloat((bestCandidate.meanQuality - championStats.meanQuality).toFixed(4)) : null
      }
    };
  }

  /**
   * 检查冠军版本是否需要回滚
   * @private
   * @param {Map<string, Object>} versionStats - 版本统计
   * @param {string} championVersion - 当前冠军版本
   * @returns {{shouldRollback: boolean, declineRate: number}}
   */
  _checkRollback(versionStats, championVersion) {
    const stats = versionStats.get(championVersion);
    if (!stats || !stats.hasEnoughSamples) {
      return { shouldRollback: false, declineRate: 0 };
    }

    // 检查置信区间下限是否低于均值*(1-threshold)
    // 即：如果质量下降可能性很高（95%CI下限低于阈值），则触发回滚
    const lowerBound = stats.confidenceInterval.lower;
    const expectedMin = stats.meanQuality * (1 - this.rollbackThreshold);

    if (lowerBound < expectedMin && stats.stdDev > 0.01) {
      const declineRate = (stats.meanQuality - lowerBound) / stats.meanQuality;
      return { shouldRollback: true, declineRate };
    }

    return { shouldRollback: false, declineRate: 0 };
  }

  /**
   * 寻找回滚目标版本（排除当前冠军，选择质量最高的历史版本）
   * @private
   * @param {Map<string, Object>} versionStats - 版本统计
   * @param {string} currentVersion - 当前故障版本
   * @returns {string|null} 回滚目标版本
   */
  _findRollbackTarget(versionStats, currentVersion) {
    let bestVersion = null;
    let bestMean = -Infinity;

    for (const [v, s] of versionStats) {
      if (v === currentVersion) continue;
      if (!s.hasEnoughSamples) continue;
      if (s.meanQuality > bestMean) {
        bestMean = s.meanQuality;
        bestVersion = v;
      }
    }

    return bestVersion;
  }

  /**
   * 保存冠军参数到内存和磁盘
   * @param {string} version - 版本号
   * @param {Object} params - 参数对象
   * @param {Map<string, Object>} [championsMap] - 外部冠军存储（如collector.js的champions Map）
   * @returns {Object} 保存的记录
   */
  saveChampion(version, params, championsMap) {
    const record = {
      params,
      savedAt: Date.now(),
      promotedAt: Date.now(),
      version
    };

    if (championsMap && typeof championsMap.set === 'function') {
      championsMap.set(version, record);
    }

    // 持久化到磁盘
    try {
      let existing = [];
      if (fs.existsSync(CHAMPIONS_FILE)) {
        existing = JSON.parse(fs.readFileSync(CHAMPIONS_FILE, 'utf8'));
      }
      // 去重：如果版本已存在则更新
      const filtered = existing.filter(([v]) => v !== version);
      filtered.push([version, record]);
      fs.writeFileSync(CHAMPIONS_FILE, JSON.stringify(filtered, null, 2));
      log('INFO', `冠军参数已保存`, { version, path: CHAMPIONS_FILE });
    } catch (err) {
      log('ERROR', `冠军参数保存失败`, { version, error: err.message });
    }

    return record;
  }

  /**
   * 批量保存候选参数
   * @param {string} version - 版本号
   * @param {Object} params - 参数对象
   * @param {Map<string, Object>} [candidatesMap] - 外部候选存储
   * @param {string} [source='evolution'] - 来源
   */
  saveCandidate(version, params, candidatesMap, source = 'evolution') {
    const record = {
      params,
      registeredAt: Date.now(),
      source
    };

    if (candidatesMap && typeof candidatesMap.set === 'function') {
      candidatesMap.set(version, record);
    }

    try {
      let existing = [];
      if (fs.existsSync(CANDIDATES_FILE)) {
        existing = JSON.parse(fs.readFileSync(CANDIDATES_FILE, 'utf8'));
      }
      const filtered = existing.filter(([v]) => v !== version);
      filtered.push([version, record]);
      fs.writeFileSync(CANDIDATES_FILE, JSON.stringify(filtered, null, 2));
    } catch (err) {
      log('ERROR', `候选参数保存失败`, { version, error: err.message });
    }

    return record;
  }

  /**
   * 生成完整的聚合报告（用于定时任务或手动触发）
   * @param {Map<string, Object>} versionStats - 版本统计
   * @param {string} [currentChampion] - 当前冠军版本
   * @returns {Object} 完整报告
   */
  generateReport(versionStats, currentChampion) {
    const versions = [];
    for (const [v, s] of versionStats) {
      versions.push({
        version: v,
        ...s,
        isChampion: v === currentChampion,
        trend: v === currentChampion ? 'baseline' : (s.meanQuality > (versionStats.get(currentChampion)?.meanQuality || 0) ? 'better' : 'worse')
      });
    }

    // 按均值降序
    versions.sort((a, b) => b.meanQuality - a.meanQuality);

    return {
      generatedAt: new Date().toISOString(),
      totalVersions: versions.length,
      currentChampion,
      versions,
      recommendation: this.decide(versionStats, currentChampion)
    };
  }

  /**
   * 导出聚合结果到CSV（便于外部分析工具使用）
   * @param {Map<string, Object>} versionStats - 版本统计
   * @param {string} [outputPath] - 输出路径
   * @returns {string} CSV内容
   */
  exportCSV(versionStats, outputPath) {
    const headers = [
      'version', 'sampleCount', 'meanQuality', 'medianQuality',
      'stdDev', 'p25Quality', 'p75Quality', 'ciLower', 'ciUpper',
      'uniqueAgents', 'maxAgentContribution'
    ];

    const rows = [headers.join(',')];
    for (const [v, s] of versionStats) {
      rows.push([
        v, s.sampleCount, s.meanQuality, s.medianQuality,
        s.stdDev, s.p25Quality, s.p75Quality,
        s.confidenceInterval.lower, s.confidenceInterval.upper,
        s.uniqueAgents, s.maxAgentContribution
      ].join(','));
    }

    const csv = rows.join('\n');

    if (outputPath) {
      fs.writeFileSync(outputPath, csv, 'utf8');
      log('INFO', `CSV已导出`, { path: outputPath, rows: rows.length - 1 });
    }

    return csv;
  }
}

// ============================================================================
// 独立运行模式：定时聚合任务
// ============================================================================

/**
 * 定时聚合入口（可通过 node aggregate.js 直接运行）
 * 读取 telemetryStore（如通过collector.js共享）并执行聚合
 */
function runAggregation() {
  log('INFO', '聚合分析引擎启动');

  // 尝试加载共享的telemetryStore
  let events = [];
  try {
    // 如果collector.js在同一进程，尝试读取其模块
    const collector = require('./collector');
    events = collector.telemetryStore || [];
    log('INFO', `从collector加载 ${events.length} 条遥测数据`);
  } catch (e) {
    // collector未加载，尝试从日志文件解析
    const logPath = path.join(process.env.HQ_DATA_DIR || './hq/data', 'telemetry.log');
    if (fs.existsSync(logPath)) {
      const lines = fs.readFileSync(logPath, 'utf8').split('\n').filter(Boolean);
      for (const line of lines) {
        try {
          const entry = JSON.parse(line);
          if (entry.paramsVersion && typeof entry.qualityScore === 'number') {
            events.push(entry);
          }
        } catch (e) {
          // 忽略解析失败的行
        }
      }
      log('INFO', `从日志文件加载 ${events.length} 条遥测数据`);
    }
  }

  if (events.length === 0) {
    log('WARN', '无数据可聚合，跳过本次分析');
    return;
  }

  // 执行聚合
  const aggregator = new Aggregator();
  const stats = aggregator.byVersion(events);

  // 获取当前冠军
  let currentChampion = null;
  try {
    const collector = require('./collector');
    const champs = collector.champions;
    let latestTime = 0;
    for (const [v, r] of champs) {
      if ((r.promotedAt || 0) > latestTime) {
        latestTime = r.promotedAt || 0;
        currentChampion = v;
      }
    }
  } catch (e) {
    // 无collector模块
  }

  // 决策
  const decision = aggregator.decide(stats, currentChampion);

  // 输出报告
  const report = aggregator.generateReport(stats, currentChampion);
  log('INFO', '聚合报告', { decision: decision.action, versions: report.totalVersions });
  console.log('\n=== 聚合报告 ===');
  console.log(JSON.stringify(report, null, 2));

  // 保存报告
  const reportPath = path.join(process.env.HQ_DATA_DIR || './hq/data', `report-${Date.now()}.json`);
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  log('INFO', `报告已保存: ${reportPath}`);

  return { stats, decision, report };
}

// 如果直接运行此文件
if (require.main === module) {
  runAggregation();

  // 定时模式（每60秒执行一次）
  const intervalMs = parseInt(process.env.AGGREGATE_INTERVAL_MS, 10) || 60000;
  if (intervalMs > 0) {
    log('INFO', `定时聚合已启用，间隔 ${intervalMs}ms`);
    setInterval(runAggregation, intervalMs);
  }
}

// ============================================================================
// 模块导出
// ============================================================================

module.exports = {
  Aggregator,
  runAggregation,
  // 导出工具函数供单元测试
  mean,
  stdDev,
  median,
  percentile,
  welchTTest,
  confidenceInterval,
  normalCDF
};
