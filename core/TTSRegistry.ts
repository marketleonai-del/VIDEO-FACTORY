/**
 * TTSRegistry.ts — TTS/声纹克隆注册表 + 能力驱动选型
 *
 * 与 ModelRegistry 同构。声纹一致性的关键：若需克隆，只在"支持克隆"的 provider 里选。
 */
import { TTSProvider } from "./TTSProvider";
import { UVGError } from "./types";

export interface TTSSelectionNeed {
  language?: string;
  /** 是否需要声纹克隆 */
  needClone?: boolean;
  /** 是否需要情感控制 */
  needEmotion?: boolean;
  /** 偏好本地/开源（更省） */
  preferLocal?: boolean;
}

export class TTSRegistry {
  private providers = new Map<string, TTSProvider>();

  register(p: TTSProvider): void {
    this.providers.set(p.providerId, p);
  }

  get(providerId: string): TTSProvider {
    const p = this.providers.get(providerId);
    if (!p) throw new UVGError(`未注册的 TTS ${providerId}`, "MODEL_NOT_FOUND");
    return p;
  }

  list(): TTSProvider[] {
    return [...this.providers.values()];
  }

  candidates(need: TTSSelectionNeed): TTSProvider[] {
    return this.list().filter((p) => {
      const c = p.capabilities;
      if (need.needClone && !c.voiceCloneSupport) return false;
      if (need.needEmotion && !c.emotionControl) return false;
      if (need.language && c.languages.length && !c.languages.includes(need.language)) return false;
      return true;
    });
  }

  /** 选型：返回 [首选, 回退...]；省钱优先，本地/开源(0费)通常排前 */
  select(need: TTSSelectionNeed): TTSProvider[] {
    const cands = this.candidates(need);
    if (!cands.length) {
      throw new UVGError(`没有满足需求的 TTS: ${JSON.stringify(need)}`, "MODEL_NOT_FOUND");
    }
    return cands
      .map((p) => ({ p, score: this.score(p, need) }))
      .sort((a, b) => b.score - a.score)
      .map((s) => s.p);
  }

  private score(p: TTSProvider, need: TTSSelectionNeed): number {
    const c = p.capabilities;
    const cheap = 1 / (c.costPerThousandChars + 0.01);
    const quality = (c.cloneQuality === "zero-shot" ? 2 : c.cloneQuality === "few-shot" ? 1 : 0) + (c.emotionControl ? 1 : 0);
    const localBonus = need.preferLocal && c.deploymentType === "local-self-hosted" ? 3 : 0;
    return cheap * 1.5 + quality + localBonus;
  }
}
