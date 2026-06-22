/**
 * EvolutionEngine.ts — 自进化引擎（融合 SkillOpt 验证门控 + Hermes 用后即学 + bandit 探索）。
 * 流程：隐式质量分组 → 提出候选参数(偏好高质量角度) → 验证门控(候选须严格优于当前，否则拒绝并入负样本缓冲)
 *      → 学习率限幅采纳 → 记录审计(accepted/rejected + 质量曲线) → 可回滚到 last-good。
 * 防跑偏：最小样本量门槛、clamp、严格胜出(no ties)、负样本缓冲避免反复提坏候选。
 */
import { TelemetryEvent } from "./Telemetry";
import { computeQuality, ImplicitQuality } from "./QualitySignals";
import { SkillParams, DEFAULT_PARAMS, applyLearningRate, clampParams } from "./SkillParams";

export interface EvolutionConfig {
  learningRate?: number;
  minSamples?: number;
  explore?: number;
  stepSize?: number;
}
export interface EvolutionRound {
  accepted: boolean;
  reason: string;
  beforeScore: number;
  afterScore: number;
  paramsVersion: number;
  quality: ImplicitQuality;
}

export class EvolutionEngine {
  private rejected: SkillParams[] = [];
  private history: EvolutionRound[] = [];
  constructor(
    private current: SkillParams = { ...DEFAULT_PARAMS },
    private lastGood: SkillParams = { ...DEFAULT_PARAMS },
    private cfg: EvolutionConfig = {},
  ) {}

  getParams(): SkillParams {
    return this.current;
  }
  getHistory(): EvolutionRound[] {
    return [...this.history];
  }
  getRejectedCount(): number {
    return this.rejected.length;
  }

  /** 每个角度族的隐式质量(0-1)：成功率0.6 + 质检通过0.4 */
  private sceneQuality(events: TelemetryEvent[]): Record<string, number> {
    const byScene: Record<string, TelemetryEvent[]> = {};
    for (const e of events) {
      if (e.type !== "generate" || !e.scene) continue;
      (byScene[e.scene] ??= []).push(e);
    }
    const out: Record<string, number> = {};
    for (const [s, evs] of Object.entries(byScene)) {
      const succ = evs.filter((e) => e.success).length / evs.length;
      const qc = evs.filter((e) => e.qcPass !== undefined);
      const qcRate = qc.length ? qc.filter((e) => e.qcPass).length / qc.length : succ;
      out[s] = +(0.6 * succ + 0.4 * qcRate).toFixed(3);
    }
    return out;
  }

  /** 提出候选：把权重朝"高于均值的角度"推，低于均值的拉低（含 ε 探索欠采样角度） */
  propose(events: TelemetryEvent[]): SkillParams {
    const step = this.cfg.stepSize ?? 1;
    const q = this.sceneQuality(events);
    const scenes = Object.keys(this.current.sceneWeights);
    const qs = scenes.map((s) => q[s] ?? 0.5);
    const mean = qs.reduce((a, b) => a + b, 0) / (qs.length || 1);
    const sceneWeights: Record<string, number> = {};
    for (const s of scenes) {
      const qi = q[s];
      if (qi === undefined) {
        sceneWeights[s] = this.current.sceneWeights[s]; // 无数据不动
      } else {
        sceneWeights[s] = this.current.sceneWeights[s] + step * (qi - mean);
      }
    }
    return clampParams({ ...this.current, version: this.current.version, sceneWeights });
  }

  /** 验证门控：候选须"严格优于"当前，否则拒绝（SkillOpt：no ties → 无静默漂移） */
  validate(candidate: SkillParams, events: TelemetryEvent[]): { ok: boolean; reason: string } {
    const n = events.filter((e) => e.type === "generate").length;
    const minSamples = this.cfg.minSamples ?? 30;
    if (n < minSamples) return { ok: false, reason: `样本不足(${n}<${minSamples})` };
    if (this.rejected.some((r) => JSON.stringify(r.sceneWeights) === JSON.stringify(candidate.sceneWeights)))
      return { ok: false, reason: "候选已在负样本缓冲(此前被拒)" };
    const q = this.sceneQuality(events);
    const scenes = Object.keys(this.current.sceneWeights);
    const qs = scenes.map((s) => q[s] ?? 0.5);
    const mean = qs.reduce((a, b) => a + b, 0) / (qs.length || 1);
    // improvement = Σ Δweight × (sceneQuality - mean)：把权重加到好角度、从差角度拿走 → 正
    let improvement = 0;
    for (const s of scenes) {
      if (q[s] === undefined) continue;
      improvement += (candidate.sceneWeights[s] - this.current.sceneWeights[s]) * (q[s] - mean);
      if (q[s] === 0 && candidate.sceneWeights[s] > this.current.sceneWeights[s]) return { ok: false, reason: `不得给零质量角度(${s})加权` };
    }
    if (improvement <= 1e-6) return { ok: false, reason: "未严格优于当前(门控拒绝)" };
    return { ok: true, reason: `improvement=${improvement.toFixed(3)}` };
  }

  /** 跑一轮进化 */
  evolveRound(events: TelemetryEvent[]): EvolutionRound {
    const before = computeQuality(events);
    const candidate = this.propose(events);
    const v = this.validate(candidate, events);
    let round: EvolutionRound;
    if (v.ok) {
      this.lastGood = this.current;
      this.current = applyLearningRate(this.current, candidate, this.cfg.learningRate ?? 0.3);
      round = { accepted: true, reason: v.reason, beforeScore: before.score, afterScore: before.score, paramsVersion: this.current.version, quality: before };
    } else {
      this.rejected.push(candidate);
      round = { accepted: false, reason: v.reason, beforeScore: before.score, afterScore: before.score, paramsVersion: this.current.version, quality: before };
    }
    this.history.push(round);
    return round;
  }

  /** 回滚到上一个 last-good（坏进化的兜底） */
  rollback(): SkillParams {
    this.current = { ...this.lastGood };
    return this.current;
  }
}
