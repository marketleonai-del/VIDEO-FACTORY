/**
 * QualitySignals.ts — 不靠用户打分的"隐式质量"。
 * 信号：成功率、重生成率(用户对同需求重做=不满意，↓更好)、质检通过率、拼接成功率、平均重试。
 * 合成 0-1 的 ImplicitQuality.score，用于判断"变好/变坏"与进化门控。
 */
import { TelemetryEvent } from "./Telemetry";

export interface ImplicitQuality {
  n: number;
  successRate: number;
  regenRate: number;
  qcPassRate: number;
  assembleRate: number;
  avgRetries: number;
  score: number;
}

const avg = (xs: number[]): number => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
const clamp01 = (x: number): number => Math.max(0, Math.min(1, x));

export function computeQuality(events: TelemetryEvent[]): ImplicitQuality {
  const gen = events.filter((e) => e.type === "generate");
  const n = gen.length;
  const successRate = avg(gen.map((e) => (e.success ? 1 : 0)));
  const regenRate = n ? events.filter((e) => e.type === "regenerate" || e.regenerated).length / n : 0;
  const qcEvents = gen.filter((e) => e.qcPass !== undefined);
  const qcPassRate = qcEvents.length ? avg(qcEvents.map((e) => (e.qcPass ? 1 : 0))) : successRate;
  const asmEvents = events.filter((e) => e.assembleOk !== undefined);
  const assembleRate = asmEvents.length ? avg(asmEvents.map((e) => (e.assembleOk ? 1 : 0))) : 1;
  const avgRetries = avg(gen.map((e) => e.retries ?? 0));

  const score = clamp01(
    0.35 * successRate + 0.3 * qcPassRate + 0.2 * assembleRate - 0.1 * Math.min(1, regenRate) - 0.05 * Math.min(1, avgRetries / 3),
  );
  return { n, successRate: +successRate.toFixed(3), regenRate: +regenRate.toFixed(3), qcPassRate: +qcPassRate.toFixed(3), assembleRate: +assembleRate.toFixed(3), avgRetries: +avgRetries.toFixed(2), score: +score.toFixed(3) };
}
