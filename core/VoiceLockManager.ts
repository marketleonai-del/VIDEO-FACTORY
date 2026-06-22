/**
 * VoiceLockManager.ts — @voice1 声纹锁管理器（核心机制）
 *
 * 解决"AI 钩子口播 与 后续真素材卖点 音色不一致"：
 *   全片只用一个锁定声纹 @voice1。无论底下换哪个 TTS，对上层都是同一个 lockId。
 *   - 有真人素材 → 克隆其声为 @voice1
 *   - 无真人素材 → 选一个内置音色为 @voice1
 *   矩阵 N 条统一用 @voice1 合成 → 音色天然一致。克隆一次，全矩阵复用。
 */
import { TTSRegistry } from "./TTSRegistry";
import { AudioResult, LockedVoice, SynthesizeParams, UVGError, VoiceSource } from "./types";

export class VoiceLockManager {
  private voices = new Map<string, LockedVoice>();

  constructor(private ttsRegistry: TTSRegistry) {}

  /**
   * 锁定一个声纹。
   * @param lockId 逻辑名，如 "voice1"
   * @param source 克隆源 或 内置音色
   * @param preferredProvider 优先使用的 TTS（可选）
   */
  async lockVoice(lockId: string, source: VoiceSource, preferredProvider?: string): Promise<LockedVoice> {
    let locked: LockedVoice;

    if (source.kind === "clone") {
      // 选一个支持克隆的 provider（优先 preferred；否则按"省钱+本地优先"自动选）
      const provider = preferredProvider
        ? this.ttsRegistry.get(preferredProvider)
        : this.ttsRegistry.select({ needClone: true, language: source.language, preferLocal: true })[0];
      if (!provider.capabilities.voiceCloneSupport || !provider.cloneVoice) {
        throw new UVGError(`${provider.providerId} 不支持克隆，无法锁定 ${lockId}`, "VOICE_LOCK_ERROR");
      }
      const info = await provider.cloneVoice({
        sampleAudio: source.sampleAudio,
        name: source.name,
        language: source.language,
      });
      if (!info) throw new UVGError(`克隆失败：${lockId}`, "VOICE_LOCK_ERROR");
      locked = {
        lockId,
        providerId: provider.providerId,
        voiceId: info.voiceId,
        cloned: true,
        language: source.language,
      };
    } else {
      // 内置音色
      const provider = this.ttsRegistry.get(source.providerId);
      locked = {
        lockId,
        providerId: provider.providerId,
        voiceId: source.voiceId,
        cloned: false,
      };
    }

    this.voices.set(lockId, locked);
    return locked;
  }

  getVoice(lockId: string): LockedVoice | undefined {
    return this.voices.get(lockId);
  }

  /** 用锁定声纹合成一段语音 */
  async synthesizeWithVoice(lockId: string, text: string, params?: Partial<SynthesizeParams>): Promise<AudioResult> {
    const v = this.requireVoice(lockId);
    const provider = this.ttsRegistry.get(v.providerId);
    return provider.synthesize({
      text,
      voiceId: v.voiceId,
      language: v.language,
      ...params,
    });
  }

  /** 批量合成：用于矩阵的统一音轨（全部同一声纹 → 音色一致） */
  async batchSynthesize(lockId: string, texts: string[], params?: Partial<SynthesizeParams>): Promise<AudioResult[]> {
    const out: AudioResult[] = [];
    for (const t of texts) {
      out.push(await this.synthesizeWithVoice(lockId, t, params));
    }
    return out;
  }

  private requireVoice(lockId: string): LockedVoice {
    const v = this.voices.get(lockId);
    if (!v) throw new UVGError(`声纹 ${lockId} 未锁定`, "VOICE_LOCK_ERROR");
    return v;
  }
}
