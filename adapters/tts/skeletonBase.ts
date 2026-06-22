/**
 * skeletonBase.ts — TTS 骨架适配器基类
 * P1/P2 TTS 适配器继承它：能力画像已填，synthesize/cloneVoice 留 TODO。
 */
import { BaseTTSProvider } from "../../core/TTSProvider";
import { AudioResult, CloneParams, SynthesizeParams, TTSCapabilities, UVGError, VoiceInfo } from "../../core/types";

export type Tier = "P0" | "P1" | "P2";

export class SkeletonTTSProvider extends BaseTTSProvider {
  constructor(
    readonly providerId: string,
    readonly providerName: string,
    readonly capabilities: TTSCapabilities,
    protected tier: Tier,
    protected demoMode = false,
  ) {
    super();
  }
  async synthesize(params: SynthesizeParams): Promise<AudioResult> {
    if (this.demoMode) return { audioUrl: `demo://${this.providerId}/audio.wav`, voiceId: params.voiceId, providerId: this.providerId };
    throw new UVGError(`${this.providerId} 适配器未接入（${this.tier}）`, "ADAPTER_NOT_IMPLEMENTED");
  }
  async cloneVoice(params: CloneParams): Promise<VoiceInfo | null> {
    if (!this.capabilities.voiceCloneSupport) return super.cloneVoice(params);
    if (this.demoMode) return { voiceId: `${this.providerId}-clone-${Date.now()}`, name: params.name, providerId: this.providerId, cloned: true };
    throw new UVGError(`${this.providerId} 克隆未接入（${this.tier}）`, "ADAPTER_NOT_IMPLEMENTED");
  }
  async healthCheck(): Promise<boolean> {
    return this.demoMode;
  }
  enableDemo(): this {
    this.demoMode = true;
    return this;
  }
}

export function baseCloudTTSCaps(languages: string[], costPerThousandChars: number, clone: boolean): TTSCapabilities {
  return {
    voiceCloneSupport: clone,
    languages,
    emotionControl: true,
    speedControl: { supported: true, min: 0.5, max: 2, step: 0.1 },
    pitchControl: { supported: false },
    costPerThousandChars,
    outputFormats: ["mp3", "wav"],
    deploymentType: "cloud-api",
    features: ["multilingual"],
  };
}
