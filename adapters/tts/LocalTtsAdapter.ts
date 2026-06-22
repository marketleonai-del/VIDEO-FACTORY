/**
 * LocalTtsAdapter.ts — 本地开源 TTS 适配器（无云 API，零调用成本）
 * 对接本机自托管的 TTS HTTP 服务（MOSS-TTS-Nano / Kokoro / Piper 均可跑成一个本地 /tts 服务）。
 * 默认主选 MOSS-TTS-Nano（0.1B·CPU·中文强·情绪·Apache-2.0 可商用）。缺服务 → demo 模式。
 * 选型依据见 references/TTS-SELECTION.md。
 */
import { BaseTTSProvider } from "../../core/TTSProvider";
import { withRetry } from "../../core/runtime";
import { AudioResult, CloneParams, SynthesizeParams, TTSCapabilities, VoiceInfo } from "../../core/types";

export type LocalTtsModel = "moss" | "kokoro" | "piper";
export interface LocalTtsConfig {
  /** 本地服务地址，如 http://localhost:9881 */
  baseUrl?: string;
  /** 是否已部署可用（false → demo 占位） */
  enabled?: boolean;
  /** 底层模型（影响能力画像默认值） */
  model?: LocalTtsModel;
}

/** 各候选模型的能力画像（零成本，本地部署） */
function capsFor(model: LocalTtsModel): TTSCapabilities {
  const base: TTSCapabilities = {
    voiceCloneSupport: false,
    languages: ["zh", "en"],
    emotionControl: false,
    speedControl: { supported: true, min: 0.5, max: 2, step: 0.1 },
    pitchControl: { supported: false },
    costPerThousandChars: 0,
    cloneCostOneTime: 0,
    outputFormats: ["wav", "mp3"],
    sampleRates: [24000, 48000],
    deploymentType: "local-self-hosted",
    features: ["open-source", "free", "offline"],
  };
  if (model === "moss")
    return { ...base, voiceCloneSupport: true, cloneQuality: "zero-shot", cloneSampleDuration: { min: 5, recommended: 8, max: 30 }, languages: ["zh", "en", "ja", "ko"], emotionControl: true, supportedEmotions: ["neutral", "happy", "sad", "serious", "gentle", "excited", "calm"], sampleRates: [48000], features: ["open-source", "free", "offline", "emotion", "zero-shot-clone", "apache-2.0"] };
  if (model === "kokoro")
    return { ...base, languages: ["zh", "en"], emotionControl: false, features: ["open-source", "free", "offline", "fast", "apache-2.0"] };
  return { ...base, features: ["open-source", "free", "offline", "ultra-light", "mit"] }; // piper
}

export class LocalTtsAdapter extends BaseTTSProvider {
  readonly providerId: string;
  readonly providerName: string;
  readonly capabilities: TTSCapabilities;
  private model: LocalTtsModel;

  constructor(private cfg: LocalTtsConfig = {}) {
    super();
    this.model = cfg.model ?? "moss";
    this.providerId = "local-tts";
    this.providerName = "本地TTS(" + this.model + ")";
    this.capabilities = capsFor(this.model);
  }
  private base(): string {
    return this.cfg.baseUrl ?? "http://localhost:9881";
  }

  async synthesize(params: SynthesizeParams): Promise<AudioResult> {
    if (!this.cfg.enabled) {
      return { audioUrl: "demo://local-tts/" + this.model + "/narration.wav", voiceId: params.voiceId, providerId: this.providerId };
    }
    return withRetry(async () => {
      // VERIFY：按所选本地 TTS 服务的接口核对字段（MOSS 自带 FastAPI；Kokoro/Piper 可用社区 HTTP 封装）
      const resp = await fetch(this.base() + "/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: params.text,
          voice: params.voiceId,
          language: params.language ?? "zh",
          emotion: params.emotion,
          speed: params.speed ?? 1,
          format: params.format ?? "wav",
        }),
      });
      if (!resp.ok) throw new Error("LocalTTS " + resp.status);
      const data = (await resp.json()) as { audio_url?: string; path?: string };
      return { audioUrl: data.audio_url ?? data.path ?? "file://local-tts-" + params.voiceId + ".wav", voiceId: params.voiceId, providerId: this.providerId, raw: data };
    });
  }

  async cloneVoice(params: CloneParams): Promise<VoiceInfo | null> {
    if (!this.capabilities.voiceCloneSupport) return super.cloneVoice(params);
    if (!this.cfg.enabled) {
      return { voiceId: "local-clone-" + Date.now(), name: params.name, providerId: this.providerId, cloned: true, language: params.language };
    }
    const resp = await fetch(this.base() + "/clone", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: params.name, sample: params.sampleAudio, language: params.language }),
    });
    if (!resp.ok) throw new Error("LocalTTS clone " + resp.status);
    const data = (await resp.json()) as { voice_id: string };
    return { voiceId: data.voice_id, name: params.name, providerId: this.providerId, cloned: true, language: params.language };
  }

  async healthCheck(): Promise<boolean> {
    return !!this.cfg.enabled;
  }
}
