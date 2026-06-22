/**
 * CosyVoiceAdapter.ts — CosyVoice 3.0 适配器（样板·真实实现，本地部署）
 *
 * 阿里通义开源，零样本克隆（3-10s 样本），中文/方言/情感，自部署≈免费。
 * 通过本地 HTTP 服务调用（默认 http://localhost:9880，按你的部署改）。缺服务 → demo 模式。
 * 成本 0：最适合"量大压成本"的矩阵场景。
 */
import { BaseTTSProvider } from "../../core/TTSProvider";
import { AudioResult, CloneParams, SynthesizeParams, TTSCapabilities, VoiceInfo } from "../../core/types";

export interface CosyVoiceConfig {
  /** 本地服务地址 */
  baseUrl?: string;
  enabled?: boolean; // 是否已部署可用
}

export class CosyVoiceAdapter extends BaseTTSProvider {
  readonly providerId = "cosyvoice";
  readonly providerName = "CosyVoice 3.0（阿里开源）";
  readonly capabilities: TTSCapabilities = {
    voiceCloneSupport: true,
    cloneQuality: "zero-shot",
    cloneSampleDuration: { min: 3, recommended: 8, max: 30 },
    languages: ["zh", "en", "ja", "yue", "ko"],
    emotionControl: true,
    supportedEmotions: ["neutral", "happy", "sad", "angry", "calm"],
    speedControl: { supported: true, min: 0.5, max: 2, step: 0.1 },
    pitchControl: { supported: true, min: -12, max: 12, step: 1 },
    costPerThousandChars: 0, // 自部署，边际成本≈0
    cloneCostOneTime: 0,
    outputFormats: ["wav", "mp3"],
    sampleRates: [22050, 44100],
    deploymentType: "local-self-hosted",
    features: ["multilingual", "dialects", "emotion", "zero-shot-clone", "free"],
  };

  constructor(private cfg: CosyVoiceConfig = {}) {
    super();
  }

  private base(): string {
    return this.cfg.baseUrl ?? "http://localhost:9880";
  }

  async synthesize(params: SynthesizeParams): Promise<AudioResult> {
    if (!this.cfg.enabled) {
      return { audioUrl: "demo://cosyvoice/narration.wav", voiceId: params.voiceId, providerId: this.providerId };
    }
    // VERIFY：按你部署的 CosyVoice 推理服务接口（如 /tts）核对字段
    const resp = await fetch(`${this.base()}/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: params.text, spk_id: params.voiceId, speed: params.speed ?? 1, emotion: params.emotion }),
    });
    if (!resp.ok) throw new Error(`CosyVoice ${resp.status}`);
    return { audioUrl: `file://cosy-${params.voiceId}.wav`, voiceId: params.voiceId, providerId: this.providerId };
  }

  async cloneVoice(params: CloneParams): Promise<VoiceInfo | null> {
    if (!this.cfg.enabled) {
      return { voiceId: `cosy-clone-${Date.now()}`, name: params.name, providerId: this.providerId, cloned: true, language: params.language };
    }
    // VERIFY：注册零样本音色（上传 3-10s 样本）
    const resp = await fetch(`${this.base()}/register_speaker`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: params.name, sample: params.sampleAudio }),
    });
    if (!resp.ok) throw new Error(`CosyVoice clone ${resp.status}`);
    const data = (await resp.json()) as { spk_id: string };
    return { voiceId: data.spk_id, name: params.name, providerId: this.providerId, cloned: true, language: params.language };
  }

  async healthCheck(): Promise<boolean> {
    return !!this.cfg.enabled;
  }
}
