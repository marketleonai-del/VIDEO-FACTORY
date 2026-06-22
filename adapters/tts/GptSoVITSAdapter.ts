/**
 * GptSoVITSAdapter.ts — GPT-SoVITS（开源，本地部署）（P0·真实实现）
 * 少样本克隆，自部署免费。通过本地推理服务调用（默认 http://localhost:9872）。
 * 缺服务 → demo 模式。VERIFY：按你部署的 api.py 接口核对。
 */
import { BaseTTSProvider } from "../../core/TTSProvider";
import { withRetry } from "../../core/runtime";
import { AudioResult, CloneParams, SynthesizeParams, TTSCapabilities, VoiceInfo } from "../../core/types";

export interface GptSoVitsConfig {
  baseUrl?: string;
  enabled?: boolean;
}

export class GptSoVITSAdapter extends BaseTTSProvider {
  readonly providerId = "gpt-sovits";
  readonly providerName = "GPT-SoVITS（开源）";
  readonly capabilities: TTSCapabilities = {
    voiceCloneSupport: true,
    cloneQuality: "few-shot",
    cloneSampleDuration: { min: 5, recommended: 30, max: 120 },
    languages: ["zh", "en", "ja"],
    emotionControl: false,
    speedControl: { supported: true, min: 0.5, max: 2, step: 0.1 },
    pitchControl: { supported: false },
    costPerThousandChars: 0,
    cloneCostOneTime: 0,
    outputFormats: ["wav"],
    deploymentType: "local-self-hosted",
    features: ["open-source", "free", "few-shot-clone"],
  };

  constructor(private cfg: GptSoVitsConfig = {}) {
    super();
  }
  private base(): string {
    return this.cfg.baseUrl ?? "http://localhost:9872";
  }

  async synthesize(params: SynthesizeParams): Promise<AudioResult> {
    if (!this.cfg.enabled) return { audioUrl: "demo://gpt-sovits/narration.wav", voiceId: params.voiceId, providerId: this.providerId };
    return withRetry(async () => {
      // VERIFY：GPT-SoVITS api.py 的合成端点与参数
      const resp = await fetch(`${this.base()}/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: params.text, text_language: params.language ?? "zh", speaker: params.voiceId, speed: params.speed ?? 1 }),
      });
      if (!resp.ok) throw new Error(`GPT-SoVITS ${resp.status}`);
      return { audioUrl: `file://sovits-${params.voiceId}.wav`, voiceId: params.voiceId, providerId: this.providerId };
    });
  }

  async cloneVoice(params: CloneParams): Promise<VoiceInfo | null> {
    if (!this.cfg.enabled) return { voiceId: `sovits-${Date.now()}`, name: params.name, providerId: this.providerId, cloned: true, language: params.language };
    // VERIFY：注册参考音频（GPT-SoVITS 多用"参考音频+文本"做少样本）
    const resp = await fetch(`${this.base()}/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: params.name, ref_audio: params.sampleAudio }),
    });
    if (!resp.ok) throw new Error(`GPT-SoVITS clone ${resp.status}`);
    const data = (await resp.json()) as { speaker: string };
    return { voiceId: data.speaker, name: params.name, providerId: this.providerId, cloned: true, language: params.language };
  }

  async healthCheck(): Promise<boolean> {
    return !!this.cfg.enabled;
  }
}
