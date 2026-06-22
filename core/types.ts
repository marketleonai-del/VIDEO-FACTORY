/**
 * types.ts — 通用视频生成 Skill 的全局类型契约
 *
 * 这是整个 Skill 的"通用语言"。业务层、抽象层、适配层都依赖这里的类型，
 * 从而做到：上层只认这些通用类型，不认任何具体模型/厂商。
 *
 * 设计原则：能力驱动（capability-driven）。所有调度都读 *Capabilities，
 * 不硬编码模型名称。
 */

/* ──────────────────────────── 通用基础 ──────────────────────────── */

/** 预算档：决定 AI 用量与降级/升级策略 */
export type BudgetTier = "minimal" | "standard" | "premium";

/** 目标平台：影响画幅/节奏/CTA */
export type Platform = "douyin" | "tiktok" | "xiaohongshu" | "videohao";

/** 部署方式 */
export type DeploymentType = "cloud-api" | "local-self-hosted" | "browser-based";

/** 统一成本估算结果（美元） */
export interface CostEstimate {
  /** 估算总价（USD） */
  totalUsd: number;
  /** 明细：每一项的来源与金额 */
  breakdown: Array<{ item: string; usd: number; note?: string }>;
  /** 货币（默认 USD） */
  currency?: string;
  /** 是否为"待核实"估算（端点/计费未确认时为 true） */
  approximate?: boolean;
}

/** 通用错误类型 */
export class UVGError extends Error {
  constructor(
    message: string,
    public readonly code:
      | "MODEL_NOT_FOUND"
      | "CAPABILITY_UNSUPPORTED"
      | "ADAPTER_NOT_IMPLEMENTED"
      | "API_ERROR"
      | "VALIDATION_ERROR"
      | "VOICE_LOCK_ERROR"
      | "WORKFLOW_ERROR",
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "UVGError";
  }
}

/* ──────────────────────────── 视频：能力画像 ──────────────────────────── */

/**
 * 视频模型能力画像。新增模型时必须精确填写——这是智能选型与成本估算的唯一依据。
 * 严禁瞎填：填错会导致调度选错模型、成本算错。
 */
export interface ModelCapabilities {
  /** 生成模式 */
  generateModes: Array<"text2video" | "image2video" | "video2video">;
  /** 单次最大秒数 */
  maxDuration: number;
  /** 单次最小秒数 */
  minDuration: number;
  /** 时长粒度（秒） */
  durationStep: number;
  /** 支持分辨率，如 ["720p","1080p","4K"] */
  resolutions: string[];
  /** 支持画幅，如 ["9:16","16:9","1:1","4:3"] */
  aspectRatios: string[];
  /** 参考图能力 */
  referenceImages: {
    min: number;
    max: number;
    supportedRoles: Array<"subject" | "style" | "pose" | "background">;
  };
  /** 是否生成原生音频（重要：原生音频在混剪时通常要静音，见 VoiceLock 机制） */
  audioSupport: boolean;
  audioQuality?: string[];
  /** 一致性控制（防穿模/锁主体） */
  consistencyControl: {
    supported: boolean;
    min?: number;
    max?: number;
    default?: number;
  };
  /** 质量档位，如 ["lite","standard","pro"] */
  qualityTiers: string[];
  /** 标准档美元/秒 */
  costPerSecond: number;
  /** 各档位倍率，如 { lite:0.5, standard:1, pro:2 } */
  costTierMultiplier?: Record<string, number>;
  /** 支持语言，如 ["zh","en"] */
  supportedLanguages: string[];
  deploymentType: DeploymentType;
  /** 特殊能力标记，如 ["lip-sync","camera-control","character-consistency"] */
  features?: string[];
}

/** 通用提示词（与模型无关） */
export interface UniversalPrompt {
  /** 自然语言分镜描述 */
  description: string;
  /** 镜头/机位/运镜等结构化片段（可选） */
  shot?: {
    focal?: string;
    aperture?: string;
    camera?: string;
    composition?: string;
    movement?: string;
    lighting?: string;
  };
  /** @imageN 锚定引用，如 { image1: "产品外观图url" } */
  anchors?: Record<string, string>;
  /** 负面提示 */
  negative?: string;
  /** 通用后缀（画质/风格/合规约束） */
  suffix?: string;
}

/** 适配后的模型专属提示词（adaptPrompt 的输出） */
export interface ModelSpecificPrompt {
  modelId: string;
  /** 拼装好的最终文本提示词 */
  prompt: string;
  /** 模型专属参数（如 image_url / reference / cfg 等） */
  params: Record<string, unknown>;
}

/** 生成入参 */
export interface GenerateParams {
  mode: "text2video" | "image2video" | "video2video";
  prompt: UniversalPrompt;
  durationSec: number;
  resolution: string;
  aspectRatio: string;
  /** 参考图：角色 → url */
  referenceImages?: Array<{ role: "subject" | "style" | "pose" | "background"; url: string }>;
  qualityTier?: string;
  /** 一致性强度（若模型支持） */
  consistency?: number;
  /** 是否要求模型输出原生音频（混剪场景通常 false） */
  wantNativeAudio?: boolean;
  seed?: number;
  language?: string;
}

/** 任务状态 */
export interface TaskStatus {
  taskId: string;
  state: "queued" | "running" | "succeeded" | "failed";
  progress?: number;
  videoUrl?: string;
  error?: string;
}

/** 生成结果 */
export interface GenerateResult {
  taskId: string;
  state: "queued" | "running" | "succeeded" | "failed";
  videoUrl?: string;
  /** 是否含原生音频（决定后续是否静音） */
  hasNativeAudio?: boolean;
  costActual?: number;
  modelId: string;
  raw?: unknown;
}

/* ──────────────────────────── TTS：能力画像 ──────────────────────────── */

export interface TTSCapabilities {
  /** 是否支持声纹克隆 */
  voiceCloneSupport: boolean;
  cloneQuality?: "zero-shot" | "few-shot" | "fine-tune";
  cloneSampleDuration?: { min: number; recommended: number; max: number };
  languages: string[];
  emotionControl: boolean;
  supportedEmotions?: string[];
  speedControl: { supported: boolean; min?: number; max?: number; step?: number };
  pitchControl: { supported: boolean; min?: number; max?: number; step?: number };
  /** 美元/千字符 */
  costPerThousandChars: number;
  /** 克隆一次性费用（USD），开源自部署可填 0 */
  cloneCostOneTime?: number;
  outputFormats: string[];
  sampleRates?: number[];
  deploymentType: DeploymentType;
  features?: string[];
}

export interface VoiceInfo {
  voiceId: string;
  name: string;
  providerId: string;
  language?: string;
  /** 是否为克隆音色 */
  cloned?: boolean;
  previewUrl?: string;
}

export interface SynthesizeParams {
  text: string;
  /** 使用的音色 id（厂商内的音色或克隆音色） */
  voiceId: string;
  language?: string;
  emotion?: string;
  speed?: number;
  pitch?: number;
  format?: string;
  sampleRate?: number;
}

export interface CloneParams {
  /** 样本音频 url 或本地路径 */
  sampleAudio: string;
  /** 给克隆音色起的名字 */
  name: string;
  language?: string;
}

export interface AudioResult {
  audioUrl: string;
  durationSec?: number;
  voiceId: string;
  providerId: string;
  costActual?: number;
  raw?: unknown;
}

/* ──────────────────────────── 声纹锁 ──────────────────────────── */

/** 声纹来源：克隆 or 选用内置音色 */
export type VoiceSource =
  | { kind: "clone"; sampleAudio: string; name: string; language?: string }
  | { kind: "builtin"; providerId: string; voiceId: string };

/** 锁定后的声纹：与具体 TTS 解耦的稳定句柄 */
export interface LockedVoice {
  /** 逻辑名，如 "voice1" */
  lockId: string;
  /** 实际承载它的 TTS providerId */
  providerId: string;
  /** provider 内的音色 id */
  voiceId: string;
  cloned: boolean;
  language?: string;
}
