/**
 * CostEstimator.ts — 统一成本估算
 *
 * 任何模型/TTS 组合都能算出一条（或一个矩阵）的总成本。
 * 体现"降本"策略：AI 只做钩子秒数、真素材拼接 0 成本、声纹克隆一次性摊薄。
 */
import { ModelRegistry } from "./ModelRegistry";
import { TTSRegistry } from "./TTSRegistry";
import { CostEstimate, GenerateParams, SynthesizeParams } from "./types";

/** 一条视频的生产计划 */
export interface ProductionPlan {
  /** AI 视频片段（通常只有钩子 0-3s） */
  aiVideoSegments: Array<{ modelId: string; params: GenerateParams }>;
  /** 真素材片段（计 0 成本，仅用于说明时长占比） */
  realFootageSec?: number;
  /** 旁白合成 */
  narration?: { providerId: string; params: SynthesizeParams };
  /** 一次性声纹克隆（整个矩阵只算一次） */
  voiceCloneOnce?: { providerId: string };
}

export class CostEstimator {
  constructor(private models: ModelRegistry, private tts: TTSRegistry) {}

  /** 估算单条 */
  estimateOne(plan: ProductionPlan, includeCloneOnce = true): CostEstimate {
    const breakdown: CostEstimate["breakdown"] = [];
    let total = 0;
    let approx = false;

    for (const seg of plan.aiVideoSegments) {
      const est = this.models.get(seg.modelId).estimateCost(seg.params);
      total += est.totalUsd;
      approx = approx || !!est.approximate;
      breakdown.push(...est.breakdown);
    }
    if (plan.realFootageSec) {
      breakdown.push({ item: `真素材拼接 ${plan.realFootageSec}s`, usd: 0, note: "复用，无 AI 成本" });
    }
    if (plan.narration) {
      const est = this.tts.get(plan.narration.providerId).estimateCost(plan.narration.params);
      total += est.totalUsd;
      approx = approx || !!est.approximate;
      breakdown.push(...est.breakdown);
    }
    if (includeCloneOnce && plan.voiceCloneOnce) {
      const c = this.tts.get(plan.voiceCloneOnce.providerId).capabilities.cloneCostOneTime ?? 0;
      total += c;
      breakdown.push({ item: "声纹克隆（一次性）", usd: c });
    }
    return { totalUsd: +total.toFixed(4), currency: "USD", approximate: approx, breakdown };
  }

  /**
   * 估算整个矩阵：N 条。声纹克隆只算一次（一次性投入摊薄）。
   * 体现"N 越大单条越便宜"。
   */
  estimateMatrix(perVariant: ProductionPlan, n: number): CostEstimate {
    const one = this.estimateOne(perVariant, false);
    const cloneOnce = perVariant.voiceCloneOnce
      ? this.tts.get(perVariant.voiceCloneOnce.providerId).capabilities.cloneCostOneTime ?? 0
      : 0;
    const total = +(one.totalUsd * n + cloneOnce).toFixed(4);
    return {
      totalUsd: total,
      currency: "USD",
      approximate: one.approximate,
      breakdown: [
        { item: `单条 × ${n}`, usd: +(one.totalUsd * n).toFixed(4) },
        { item: "声纹克隆（整矩阵一次）", usd: cloneOnce },
        ...one.breakdown.map((b) => ({ ...b, note: "单条明细" })),
      ],
    };
  }
}
