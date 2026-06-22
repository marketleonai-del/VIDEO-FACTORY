/**
 * skeletons.ts — P1/P2 视频骨架适配器（能力画像完整，调用待接入）
 * 每个都已具备：精确能力画像（选型/成本/健康检查可用）+ 统一接口骨架 + 清晰 TODO。
 * 接入 = 子类化 SkeletonVideoModel 覆写 doGenerate，或在此就地实现 fetch。
 */
import { ModelCapabilities } from "../../core/types";
import { SkeletonVideoModel, baseCloudCaps } from "./skeletonBase";

/* P1 · 应实现 */

/** Wan 2.6：开源/本地，最省（~$0.005/s） */
const wanCaps: ModelCapabilities = {
  generateModes: ["text2video", "image2video"],
  maxDuration: 10,
  minDuration: 2,
  durationStep: 1,
  resolutions: ["720p"],
  aspectRatios: ["9:16", "16:9", "1:1"],
  referenceImages: { min: 0, max: 1, supportedRoles: ["subject"] },
  audioSupport: false,
  consistencyControl: { supported: false },
  qualityTiers: ["standard"],
  costPerSecond: 0.005,
  supportedLanguages: ["zh", "en"],
  deploymentType: "local-self-hosted",
  features: ["low-cost", "open-source"],
};
export const WanAdapter = new SkeletonVideoModel("wan", "Wan 2.6", wanCaps, "P1");
export const GeminiOmniAdapter = new SkeletonVideoModel("gemini-omni", "Gemini Omni", baseCloudCaps(["text2video"], 0.12), "P1");
export const CodexAdapter = new SkeletonVideoModel("codex", "Codex（视频）", baseCloudCaps(["text2video"], 0.1), "P1");
export const WukongAdapter = new SkeletonVideoModel("wukong", "悟空（字节）", baseCloudCaps(["text2video", "image2video"], 0.1), "P1");

/* P2 · 预留（待调研 API） */
export const WokebuddyAdapter = new SkeletonVideoModel("wokebuddy", "Wokebuddy", baseCloudCaps(["text2video"], 0.1), "P2");
export const QclawAdapter = new SkeletonVideoModel("qclaw", "Qclaw", baseCloudCaps(["text2video"], 0.1), "P2");
export const OpenclawAdapter = new SkeletonVideoModel("openclaw", "Openclaw", baseCloudCaps(["text2video"], 0.1), "P2");
/** Sora 已 2026-04 关停，仅留接口占位，不建议启用 */
export const SoraAdapter = new SkeletonVideoModel("sora", "Sora（已关停）", baseCloudCaps(["text2video"], 0.1), "P2");

export const VIDEO_SKELETONS = [WanAdapter, GeminiOmniAdapter, CodexAdapter, WukongAdapter, WokebuddyAdapter, QclawAdapter, OpenclawAdapter, SoraAdapter];
