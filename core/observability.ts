/**
 * observability.ts — 结构化 JSON 日志 + 指标 + 请求 ID（商用可观测性）
 */
import { randomUUID } from "node:crypto";

export type Level = "debug" | "info" | "warn" | "error";
const ORD: Record<Level, number> = { debug: 10, info: 20, warn: 30, error: 40 };

/** 结构化 JSON 日志器；child() 携带上下文字段（如 requestId/tenant） */
export class JsonLogger {
  constructor(
    private level: Level = ((process.env.UVG_LOG_LEVEL as Level) || "info"),
    private base: Record<string, unknown> = {},
  ) {}
  child(fields: Record<string, unknown>): JsonLogger {
    return new JsonLogger(this.level, { ...this.base, ...fields });
  }
  private emit(l: Level, msg: string, fields?: Record<string, unknown>): void {
    if ((ORD[l] ?? 99) < (ORD[this.level] ?? 20)) return;
    const rec = { ts: new Date().toISOString(), level: l, msg, ...this.base, ...fields };
    const line = JSON.stringify(rec);
    (l === "error" ? console.error : l === "warn" ? console.warn : console.log)(line);
  }
  debug(m: string, f?: Record<string, unknown>): void { this.emit("debug", m, f); }
  info(m: string, f?: Record<string, unknown>): void { this.emit("info", m, f); }
  warn(m: string, f?: Record<string, unknown>): void { this.emit("warn", m, f); }
  error(m: string, f?: Record<string, unknown>): void { this.emit("error", m, f); }
}

/** 轻量指标：计数器 + 计时器（count/avg）；snapshot 供 /metrics */
export class Metrics {
  private counters = new Map<string, number>();
  private timers = new Map<string, { count: number; sumMs: number }>();
  inc(name: string, n = 1): void { this.counters.set(name, (this.counters.get(name) ?? 0) + n); }
  observe(name: string, ms: number): void {
    const t = this.timers.get(name) ?? { count: 0, sumMs: 0 };
    t.count++; t.sumMs += ms; this.timers.set(name, t);
  }
  snapshot(): { counters: Record<string, number>; timers: Record<string, { count: number; avgMs: number }> } {
    return {
      counters: Object.fromEntries(this.counters),
      timers: Object.fromEntries([...this.timers].map(([k, v]) => [k, { count: v.count, avgMs: v.count ? +(v.sumMs / v.count).toFixed(1) : 0 }])),
    };
  }
}

export function newRequestId(): string {
  return randomUUID();
}
export const metrics = new Metrics();
