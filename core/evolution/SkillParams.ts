/**
 * SkillParams.ts — 技能的"可训练参数"（SkillOpt 思路：把可调项当作可优化参数，版本化、门控更新）。
 * 这些权重影响选型/角度/质检门；进化引擎据隐式质量更新它们（带学习率，限制每轮漂移）。
 */
export interface SkillParams {
  version: number;
  /** 角度族/场景 → 权重（0-2，越高越被偏好） */
  sceneWeights: Record<string, number>;
  /** 模型 → 选型偏置（0-2） */
  modelBias: Record<string, number>;
  /** 通用提示词后缀（可进化的文本参数） */
  promptSuffix: string;
  /** 质检门（可进化，90-115） */
  qualityThreshold: number;
}

export const DEFAULT_PARAMS: SkillParams = {
  version: 1,
  sceneWeights: { 痛点放大: 1, 反常识: 1, 身份认同: 1, 省钱算账: 1, 信任背书: 1, 情绪共鸣: 1 },
  modelBias: {},
  promptSuffix: "手机实拍质感, no text",
  qualityThreshold: 100,
};

/** 防跑偏：把参数夹到安全范围 */
export function clampParams(p: SkillParams): SkillParams {
  const cw = (w: Record<string, number>): Record<string, number> =>
    Object.fromEntries(Object.entries(w).map(([k, v]) => [k, Math.max(0, Math.min(2, v))]));
  return {
    ...p,
    sceneWeights: cw(p.sceneWeights),
    modelBias: cw(p.modelBias),
    qualityThreshold: Math.max(90, Math.min(115, p.qualityThreshold)),
  };
}

/**
 * 文本学习率（SkillOpt 思路）：候选只朝当前移动 lr 比例，限制每轮漂移。
 * 数值参数线性插值；文本后缀仅当 lr≥0.5 才采纳候选（避免频繁抖动）。
 */
export function applyLearningRate(current: SkillParams, candidate: SkillParams, lr = 0.3): SkillParams {
  const blend = (a: Record<string, number>, b: Record<string, number>): Record<string, number> => {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    const out: Record<string, number> = {};
    for (const k of keys) {
      const av = a[k] ?? 1;
      const bv = b[k] ?? av;
      out[k] = +(av + lr * (bv - av)).toFixed(3);
    }
    return out;
  };
  return clampParams({
    version: current.version + 1,
    sceneWeights: blend(current.sceneWeights, candidate.sceneWeights),
    modelBias: blend(current.modelBias, candidate.modelBias),
    promptSuffix: lr >= 0.5 ? candidate.promptSuffix : current.promptSuffix,
    qualityThreshold: +(current.qualityThreshold + lr * (candidate.qualityThreshold - current.qualityThreshold)).toFixed(1),
  });
}
