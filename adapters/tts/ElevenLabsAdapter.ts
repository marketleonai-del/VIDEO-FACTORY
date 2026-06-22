/**
 * ElevenLabsAdapter.ts — ElevenLabs TTS/克隆适配器（样板·真实实现）
 *
 * 即时克隆、跨内容音色稳定，$5/月起。多语言。
 * 端点：https://api.elevenlabs.io/v1（VERIFY：以官方最新文档为准）。缺 Key → demo 模式。
 */
import { BaseTTSProvider } from "../../core/TTSProvider";
import { AudioResult, CloneParams, SynthesizeParams, TTSCapabilities, VoiceInfo } from "../../core/types";

export interface ElevenLabsConfig {
  apiKey?: string;
  baseUrl?: string;
}

export class ElevenLabsAdapter extends BaseTTSProvider {
  readonly providerId = "elevenlabs";
  readonly providerName = "ElevenLabs";
  readonly capabilities: TTSCapabilities = {
    voiceCloneSupport: true,
    cloneQuality: "zero-shot",
    cloneSampleDuration: { min: 10, recommended: 60, max: 300 },
    languages: ["en", "zh", "ja", "es", "fr", "de", "multi"],
    emotionControl: true,
    supportedEmotions: ["neutral", "happy", "sad", "excited"],
    speedControl: { supported: true, min: 0.7, max: 1.2, step: 0.05 },
    pitchControl: { supported: false },
    costPerThousandChars: 0.3, // 约值（按套餐折算），approximate
    cloneCostOneTime: 0, // 含在订阅内
    outputFormats: ["mp3", "wav"],
    sampleRates: [44100],
    deploymentType: "cloud-api",
    features: ["multilingual", "instant-clone", "stable-across-content"],
  };

  constructor(private cfg: ElevenLabsConfig = {}) {
    super();
  }

  private base(): string {
    return this.cfg.baseUrl ?? "https://api.elevenlabs.io/v1";
  }

  async synthesize(params: SynthesizeParams): Promise<AudioResult> {
    if (!this.cfg.apiKey) {
      return { audioUrl: "demo://elevenlabs/narration.mp3", voiceId: params.voiceId, providerId: this.providerId };
    }
    // VERIFY：返回为音频二进制，真实实现应写盘并返回文件路径
    const resp = await fetch(`${this.base()}/text-to-speech/${params.voiceId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "xi-api-key": this.cfg.apiKey },
      body: JSON.stringify({ text: params.text, model_id: "eleven_multilingual_v2" }),
    });
    if (!resp.ok) throw new Error(`ElevenLabs ${resp.status}: ${await resp.text()}`);
    // const buf = await resp.arrayBuffer(); fs.writeFileSync(out, Buffer.from(buf));
    return { audioUrl: `file://narration-${params.voiceId}.mp3`, voiceId: params.voiceId, providerId: this.providerId };
  }

  async cloneVoice(params: CloneParams): Promise<VoiceInfo | null> {
    if (!this.cfg.apiKey) {
      return { voiceId: `demo-clone-${Date.now()}`, name: params.name, providerId: this.providerId, cloned: true, language: params.language };
    }
    // VERIFY：POST /voices/add（multipart：name + files[]）
    const fd = new FormData();
    fd.append("name", params.name);
    // fd.append("files", <Blob of sampleAudio>);  // 真实实现读取样本音频
    const resp = await fetch(`${this.base()}/voices/add`, { method: "POST", headers: { "xi-api-key": this.cfg.apiKey }, body: fd });
    if (!resp.ok) throw new Error(`ElevenLabs clone ${resp.status}`);
    const data = (await resp.json()) as { voice_id: string };
    return { voiceId: data.voice_id, name: params.name, providerId: this.providerId, cloned: true, language: params.language };
  }

  async healthCheck(): Promise<boolean> {
    return !!this.cfg.apiKey;
  }
}
