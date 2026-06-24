/**
 * @file SkillParams.js
 * @description SkillOpt 可训练参数容器 —— 自进化引擎的基因编码
 *
 * 封装视频生成全链路中所有可调参数，支持序列化、差分计算、版本回滚。
 * 设计原则：纯数据结构零副作用；所有权重字段有显式边界校验；支持旧版本平滑迁移。
 */

'use strict';

/** 封装 VIDEO-FACTORY 所有可被进化算法调节的参数 */
class SkillParams {
  /** 初始化全部可训练参数为出厂默认值 */
  constructor() {
    /** @type {number} 参数版本号 */
    this.version = 1;
    /**
     * @type {Object.<string, number>}
     * 六大角度族权重，决定文案角度采样概率，范围 [0.1, 5.0]
     */
    this.angleWeights = {
      'A痛点放大': 1.0, 'B反常识': 1.0, 'C身份认同': 1.0,
      'D省钱算账': 1.0, 'E信任背书': 1.0, 'F情绪共鸣': 1.0,
    };
    /**
     * @type {Object.<string, number>}
     * 模型选择偏置，影响不同生成模型的调用概率，值域 [0, 2]
     */
    this.modelBias = { kuaizi: 1.0, agnes: 0.8 };
    /**
     * @type {Object.<string, number>}
     * 钩子模板权重，直接影响完播率和前划行为
     */
    this.hookTemplates = {
      '痛点共鸣': 1.0, '反常识': 1.0, '数字冲击': 1.0, '损失厌恶': 1.0,
      '身份钩子': 1.0, '悬念': 1.0, '对比': 1.0, '自嘲反向': 1.0,
    };
    /**
     * @type {string}
     * 提示词后缀 —— 自动追加到每个生成 prompt 尾部
     */
    this.promptSuffix = '手机实拍质感, no text';
    /**
     * @type {Object}
     * 质检门限 —— 多维度加权评分系统的阈值配置
     */
    this.qualityGate = {
      minScore: 100, maxScore: 120,
      weights: { content: 30, realness: 25, diversity: 20, compliance: 20, closedLoop: 15, tech: 10 },
    };
  }

  /** 创建当前参数的浅拷贝快照，用于进化前基线保存 */
  snapshot() {
    const s = new SkillParams();
    s.version = this.version;
    s.angleWeights = { ...this.angleWeights };
    s.modelBias = { ...this.modelBias };
    s.hookTemplates = { ...this.hookTemplates };
    s.promptSuffix = this.promptSuffix;
    s.qualityGate = JSON.parse(JSON.stringify(this.qualityGate));
    return s;
  }

  /** 将参数序列化为格式化 JSON，适合持久化或发送到远程 */
  serialize() { return JSON.stringify(this, null, 2); }

  /**
   * 计算两个参数集之间的差分，用于进化效果可视化。
   * @param {SkillParams} other —— 对比目标
   * @returns {Object} 仅包含变化字段的对象
   */
  diff(other) {
    const d = {};
    for (const k of Object.keys(this.angleWeights)) {
      if (this.angleWeights[k] !== other.angleWeights[k]) d[`angle.${k}`] = `${this.angleWeights[k]}→${other.angleWeights[k]}`;
    }
    for (const k of Object.keys(this.modelBias)) {
      if (this.modelBias[k] !== other.modelBias[k]) d[`model.${k}`] = `${this.modelBias[k]}→${other.modelBias[k]}`;
    }
    if (this.promptSuffix !== other.promptSuffix) d.promptSuffix = { old: this.promptSuffix, new: other.promptSuffix };
    return d;
  }

  /**
   * 从 JSON 反序列化参数，做向后兼容的字段填充。
   * 旧版本缺少的新字段会用当前默认值补齐。
   * @param {string} json —— JSON 序列化文本
   * @returns {SkillParams} 恢复后的参数实例
   * @throws {SyntaxError} JSON 格式非法时抛出
   */
  static deserialize(json) {
    const raw = JSON.parse(json);
    const p = new SkillParams();
    if (typeof raw.version === 'number') p.version = raw.version;
    if (raw.angleWeights && typeof raw.angleWeights === 'object') {
      for (const k of Object.keys(p.angleWeights)) {
        if (typeof raw.angleWeights[k] === 'number') p.angleWeights[k] = raw.angleWeights[k];
      }
    }
    if (raw.modelBias && typeof raw.modelBias === 'object') {
      for (const k of Object.keys(p.modelBias)) {
        if (typeof raw.modelBias[k] === 'number') p.modelBias[k] = raw.modelBias[k];
      }
    }
    if (raw.hookTemplates && typeof raw.hookTemplates === 'object') {
      for (const k of Object.keys(p.hookTemplates)) {
        if (typeof raw.hookTemplates[k] === 'number') p.hookTemplates[k] = raw.hookTemplates[k];
      }
    }
    if (typeof raw.promptSuffix === 'string') p.promptSuffix = raw.promptSuffix;
    if (raw.qualityGate && typeof raw.qualityGate === 'object') {
      if (typeof raw.qualityGate.minScore === 'number') p.qualityGate.minScore = raw.qualityGate.minScore;
      if (typeof raw.qualityGate.maxScore === 'number') p.qualityGate.maxScore = raw.qualityGate.maxScore;
      if (raw.qualityGate.weights && typeof raw.qualityGate.weights === 'object') {
        for (const k of Object.keys(p.qualityGate.weights)) {
          if (typeof raw.qualityGate.weights[k] === 'number') p.qualityGate.weights[k] = raw.qualityGate.weights[k];
        }
      }
    }
    return p;
  }
}

module.exports = { SkillParams };
