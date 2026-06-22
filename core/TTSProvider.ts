/**
 * TTSProvider.ts — TTS / 声纹克隆统一接口（抽象层契约）
 *
 * 所有 TTS / 声纹克隆工具（CosyVoice/GPT-SoVITS/火山/ElevenLabs/...）实现本接口。
 * 不支持的可选能力（如克隆）返回 null/抛 CAPABILITY_UNSUPPORTED。
 */
import {
  AudioResult,
  CloneParams,
  CostEstimate,
  SynthesizeParams,
  TTSCapabilities,
  UVGError,
  VoiceInfo,
} from "./types";

/** TTS 统一契约 */
export interface TTSProvider {
  readonly providerId: string;
  readonly providerName: string;
  readonly capabilities: TTSCapabilities;

  /** 核心：文本 → 语音 */
  synthesize(params: SynthesizeParams): Promise<AudioResult>;
  /** 声纹克隆（不支持则返回 null） */
  cloneVoice?(params: CloneParams): Promise<VoiceInfo | null>;
  /** 列出可用音色 */
  listVoices?(): Promise<VoiceInfo[]>;
  /** 删除克隆音色 */
  deleteVoice?(voiceId: string): Promise<boolean>;
  /** 成本估算 */
  estimateCost(params: SynthesizeParams | CloneParams): CostEstimate;
  /** 健康检查 */
  healthCheck(): Promise<boolean>;
}

/** TTS 适配器基类：收敛成本估算与字符计费逻辑 */
export abstract class BaseTTSProvider implements TTSProvider {
  abstract readonly providerId: string;
  abstract readonly providerName: string;
  abstract readonly capabilities: TTSCapabilities;

  abstract synthesize(params: SynthesizeParams): Promise<AudioResult>;
  abstract healthCheck(): Promise<boolean>;

  /** 默认成本估算：合成按千字符计费；克隆按一次性费用 */
  estimateCost(params: SynthesizeParams | CloneParams): CostEstimate {
    const cap = this.capabilities;
    if ("text" in params) {
      const chars = params.text.length;
      const usd = +((chars / 1000) * cap.costPerThousandChars).toFixed(4);
      return {
        totalUsd: usd,
        currency: "USD",
        approximate: cap.deploymentType !== "cloud-api",
        breakdown: [{ item: `${this.providerName} 合成 ${chars}字 × $${cap.costPerThousandChars}/千字`, usd }],
      };
    }
    const usd = cap.cloneCostOneTime ?? 0;
    return {
      totalUsd: usd,
      currency: "USD",
      approximate: cap.deploymentType !== "cloud-api",
      breakdown: [{ item: `${this.providerName} 声纹克隆（一次性）`, usd }],
    };
  }

  /** 默认克隆：不支持的 provider 抛错（子类支持则 override） */
  async cloneVoice(_params: CloneParams): Promise<VoiceInfo | null> {
    if (!this.capabilities.voiceCloneSupport) {
      throw new UVGError(`${this.providerId} 不支持声纹克隆`, "CAPABILITY_UNSUPPORTED");
    }
    return null;
  }
}
