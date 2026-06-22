/**
 * budgets.ts — 预算档预设（决定 AI 用量与降级/升级）
 * 体现"钱只花在钩子"的降本策略。
 */
import { BudgetTier } from "../core/types";

export interface BudgetPreset {
  tier: BudgetTier;
  /** 每条允许的 AI 视频秒数（其余用真素材拼接） */
  aiVideoSecondsPerVariant: number;
  useAiBRoll: boolean;
  resolution: string;
  /** 是否做口型同步（出现说话的脸时才需要；省钱默认 false） */
  lipSync: boolean;
  preferLocalModels: boolean;
  note: string;
}

export const BUDGET_PRESETS: Record<BudgetTier, BudgetPreset> = {
  minimal: {
    tier: "minimal",
    aiVideoSecondsPerVariant: 3, // 仅 3 秒钩子
    useAiBRoll: false,
    resolution: "720p",
    lipSync: false,
    preferLocalModels: true, // Wan/本地，省到极致
    note: "极省：AI 仅做 3 秒无人脸钩子 + 全真素材拼接 + 开源 TTS。单条 AI 成本≈¥0.1-1",
  },
  standard: {
    tier: "standard",
    aiVideoSecondsPerVariant: 6, // 钩子 + 1 个补镜
    useAiBRoll: true,
    resolution: "720p",
    lipSync: false,
    preferLocalModels: false,
    note: "标准（推荐）：可灵/Wan 钩子 + 真素材 + @voice1。单条 AI 成本≈¥1-5，质量对齐母版",
  },
  premium: {
    tier: "premium",
    aiVideoSecondsPerVariant: 12, // 多 AI 镜
    useAiBRoll: true,
    resolution: "1080p",
    lipSync: true, // 可上数字人口型同步
    preferLocalModels: false,
    note: "精品+：多 AI 镜头 + 口型同步数字人 + 1080p，适合头部赢家/品牌片",
  },
};
