/**
 * platforms.ts — 各平台预设（画幅/节奏/钩子/CTA）
 * 供 platform-adaptation Stage 与选型使用。
 */
import { Platform } from "../core/types";

export interface PlatformPreset {
  platform: Platform;
  aspectRatio: string;
  /** 前3秒要求 */
  hookRule: string;
  /** 节奏密度 */
  rhythm: "fast" | "medium" | "medium-fast";
  /** 默认 CTA */
  cta: string;
  /** 偏好钩子族（来自 ugc-creative-amplifier 钩子库） */
  preferredHookFamilies: string[];
  notes: string;
}

export const PLATFORM_PRESETS: Record<Platform, PlatformPreset> = {
  douyin: {
    platform: "douyin",
    aspectRatio: "9:16",
    hookRule: "强钩子/冲突/反认知，信息密度高",
    rhythm: "fast",
    cta: "小黄车",
    preferredHookFamilies: ["痛点焦虑", "反常识", "数字清单"],
    notes: "接受强口播；前3秒禁慢热",
  },
  tiktok: {
    platform: "tiktok",
    aspectRatio: "9:16",
    hookRule: "视觉冲击/音乐卡点，跨文化",
    rhythm: "fast",
    cta: "TikTok Shop",
    preferredHookFamilies: ["视觉打断", "对比反差", "情绪共鸣"],
    notes: "更吃画面，口播宜短、配多语言字幕；避免本地化梗",
  },
  xiaohongshu: {
    platform: "xiaohongshu",
    aspectRatio: "9:16",
    hookRule: "高颜值/生活美学",
    rhythm: "medium",
    cta: "评论区/挂链",
    preferredHookFamilies: ["身份圈层", "情绪共鸣", "数字清单"],
    notes: "审美优先，硬推销减分；4:3 也可",
  },
  videohao: {
    platform: "videohao",
    aspectRatio: "9:16",
    hookRule: "标题党/社交货币",
    rhythm: "medium",
    cta: "公众号/私域",
    preferredHookFamilies: ["悬念好奇", "数字清单", "痛点焦虑"],
    notes: "信息增量+转发价值；过度娱乐减分",
  },
};
