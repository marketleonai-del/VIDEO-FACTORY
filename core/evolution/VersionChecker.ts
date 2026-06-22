/**
 * VersionChecker.ts — 拉取总部发布的最优参数/版本，本地验证门控后再采纳（防止盲目升级变坏）。
 */
import { SkillParams } from "./SkillParams";
import { EvolutionEngine } from "./EvolutionEngine";
import { TelemetryEvent } from "./Telemetry";
import { logger } from "../runtime";

export interface LatestParams {
  version: number;
  params: SkillParams;
  skillVersion?: string;
}

export class VersionChecker {
  constructor(private endpoint = process.env.UVG_PARAMS_URL || "") {}

  async fetchLatest(): Promise<LatestParams | undefined> {
    if (!this.endpoint) return undefined;
    try {
      const resp = await fetch(this.endpoint);
      if (!resp.ok) return undefined;
      return (await resp.json()) as LatestParams;
    } catch (e) {
      logger.warn("拉取最新参数失败", { err: (e as Error).message });
      return undefined;
    }
  }

  /**
   * 采纳策略：仅当 HQ 版本更高、且在本地近期数据上"验证门控"也通过时才采纳；否则保留本地，可回滚。
   * 返回是否采纳 + 原因。
   */
  adopt(engine: EvolutionEngine, latest: LatestParams, recentEvents: TelemetryEvent[]): { adopted: boolean; reason: string } {
    const cur = engine.getParams();
    if (latest.version <= cur.version) return { adopted: false, reason: `HQ版本不更新(${latest.version}<=${cur.version})` };
    const v = engine.validate(latest.params, recentEvents);
    if (!v.ok) return { adopted: false, reason: `本地门控未通过：${v.reason}` };
    // 通过门控 → 采纳（通过 applyLearningRate 也可，这里直接采纳 HQ 已验证的最优）
    return { adopted: true, reason: `采纳 HQ v${latest.version}（${v.reason}）` };
  }
}
