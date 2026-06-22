/**
 * ModelRegistry.ts — 视频模型注册表 + 能力驱动的智能选型
 *
 * 插件式核心：Adapter 在这里注册；上层用"需求 + 预算"来 select，
 * 注册表按【能力画像】挑最优模型，并给出回退链。不硬编码任何模型名。
 */
import { VideoModel } from "./VideoModel";
import { BudgetTier, UVGError } from "./types";

/** 选型需求（全部基于能力，不写模型名） */
export interface SelectionNeed {
  mode: "text2video" | "image2video" | "video2video";
  aspectRatio: string;
  durationSec: number;
  language?: string;
  budgetTier?: BudgetTier;
  /** 必须具备的特性，如 ["lip-sync"] */
  requiredFeatures?: string[];
  /** 是否偏好本地/开源（更省） */
  preferLocal?: boolean;
}

export class ModelRegistry {
  private models = new Map<string, VideoModel>();

  /** 注册一个视频模型适配器 */
  register(model: VideoModel): void {
    this.models.set(model.modelId, model);
  }

  get(modelId: string): VideoModel {
    const m = this.models.get(modelId);
    if (!m) throw new UVGError(`未注册的视频模型 ${modelId}`, "MODEL_NOT_FOUND");
    return m;
  }

  list(): VideoModel[] {
    return [...this.models.values()];
  }

  /** 候选过滤：返回所有满足硬性能力的模型 */
  candidates(need: SelectionNeed): VideoModel[] {
    return this.list().filter((m) => {
      const c = m.capabilities;
      if (!c.generateModes.includes(need.mode)) return false;
      if (!c.aspectRatios.includes(need.aspectRatio)) return false;
      if (need.durationSec > c.maxDuration || need.durationSec < c.minDuration) return false;
      if (need.language && c.supportedLanguages.length && !c.supportedLanguages.includes(need.language)) return false;
      if (need.requiredFeatures?.some((f) => !(c.features ?? []).includes(f))) return false;
      return true;
    });
  }

  /**
   * 智能选型：在候选里按"性价比"打分挑最优。
   * 预算 minimal → 最便宜优先；premium → 能力/特性优先；standard → 平衡。
   * 返回 [首选, 回退1, 回退2...]，供回退机制使用。
   */
  select(need: SelectionNeed): VideoModel[] {
    const cands = this.candidates(need);
    if (!cands.length) {
      throw new UVGError(`没有满足需求的视频模型: ${JSON.stringify(need)}`, "MODEL_NOT_FOUND");
    }
    const budget = need.budgetTier ?? "standard";
    const scored = cands
      .map((m) => ({ m, score: this.score(m, need, budget) }))
      .sort((a, b) => b.score - a.score);
    return scored.map((s) => s.m);
  }

  private score(m: VideoModel, need: SelectionNeed, budget: BudgetTier): number {
    const c = m.capabilities;
    // 价格分：越便宜越高（归一化）
    const cheap = 1 / (c.costPerSecond + 0.01);
    // 能力分：特性数 + 一致性 + 最高分辨率
    const power =
      (c.features?.length ?? 0) +
      (c.consistencyControl.supported ? 1 : 0) +
      (c.resolutions.includes("4K") ? 1 : c.resolutions.includes("1080p") ? 0.5 : 0);
    const localBonus = need.preferLocal && c.deploymentType !== "cloud-api" ? 2 : 0;
    if (budget === "minimal") return cheap * 3 + power * 0.3 + localBonus;
    if (budget === "premium") return power * 3 + cheap * 0.3;
    return cheap * 1.5 + power * 1.5 + localBonus * 0.5;
  }
}
