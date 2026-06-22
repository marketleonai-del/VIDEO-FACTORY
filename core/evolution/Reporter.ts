/**
 * Reporter.ts — 把匿名遥测批量上报到总部（opt-in、可配 URL）。失败静默不影响主流程。
 */
import { TelemetryEvent } from "./Telemetry";
import { logger } from "../runtime";

export class Reporter {
  constructor(private endpoint = process.env.UVG_TELEMETRY_URL || "") {}
  enabled(): boolean {
    return !!this.endpoint;
  }
  async send(events: TelemetryEvent[]): Promise<{ ok: boolean; sent: number }> {
    if (!this.endpoint || events.length === 0) return { ok: false, sent: 0 };
    try {
      const resp = await fetch(this.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events }),
      });
      return { ok: resp.ok, sent: events.length };
    } catch (e) {
      logger.warn("telemetry 上报失败", { err: (e as Error).message });
      return { ok: false, sent: 0 };
    }
  }
}
