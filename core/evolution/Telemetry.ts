/**
 * Telemetry.ts — 匿名、可开关、无 PII 的隐式遥测（隐私第一）。
 * 默认关闭（opt-in：UVG_TELEMETRY=on）。匿名安装 id = 随机值哈希，无任何用户身份/内容。
 * 只采隐式信号（成功/重试/重新生成/质检分/拼接成功/耗时），绝不采脚本文本、素材、产品名。
 */
import { randomUUID, createHash } from "node:crypto";

export interface TelemetryEvent {
  type: "generate" | "assemble" | "regenerate" | "qc";
  anonId: string;
  skillVersion: number;
  paramsVersion: number;
  model?: string;
  scene?: string;
  angle?: string;
  durationSec?: number;
  success: boolean;
  retries?: number;
  regenerated?: boolean;
  qcScore?: number;
  qcPass?: boolean;
  assembleOk?: boolean;
  ms?: number;
  ts: number;
}

/** 上报白名单——只有这些字段会被采集/发送 */
const ALLOW: Array<keyof TelemetryEvent> = [
  "type", "anonId", "skillVersion", "paramsVersion", "model", "scene", "angle",
  "durationSec", "success", "retries", "regenerated", "qcScore", "qcPass", "assembleOk", "ms", "ts",
];

export class Telemetry {
  private buf: TelemetryEvent[] = [];
  private anonId: string;
  constructor(
    private enabled: boolean = process.env.UVG_TELEMETRY === "on",
    salt: string = process.env.UVG_TELEMETRY_SALT || "uvg",
  ) {
    this.anonId = createHash("sha256").update(randomUUID() + salt).digest("hex").slice(0, 16);
  }
  isEnabled(): boolean {
    return this.enabled;
  }
  setEnabled(b: boolean): void {
    this.enabled = b;
  }
  /** 记录一条（关闭时丢弃）。自动套白名单，杜绝误采 PII */
  record(ev: Omit<TelemetryEvent, "anonId" | "ts">): void {
    if (!this.enabled) return;
    this.buf.push(Telemetry.sanitize({ ...ev, anonId: this.anonId, ts: Date.now() }));
  }
  drain(): TelemetryEvent[] {
    const b = this.buf;
    this.buf = [];
    return b;
  }
  /** 隐私守卫：剔除白名单外的任何字段 */
  static sanitize(ev: Record<string, unknown>): TelemetryEvent {
    const out: Record<string, unknown> = {};
    for (const k of ALLOW) if (k in ev) out[k] = ev[k as string];
    return out as unknown as TelemetryEvent;
  }
}
