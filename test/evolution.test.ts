/**
 * evolution.test.ts — 自进化：参数学习率/clamp、遥测脱敏、隐式质量、进化门控/回滚
 */
import assert from "node:assert";
import { applyLearningRate, clampParams, DEFAULT_PARAMS } from "../core/evolution/SkillParams";
import { Telemetry, TelemetryEvent } from "../core/evolution/Telemetry";
import { computeQuality } from "../core/evolution/QualitySignals";
import { EvolutionEngine } from "../core/evolution/EvolutionEngine";

let passed = 0;
function test(n: string, fn: () => void): void { fn(); passed++; console.log(`  ✓ ${n}`); }

function synth(goodN: number, badN: number): TelemetryEvent[] {
  const evs: TelemetryEvent[] = [];
  for (let i = 0; i < goodN; i++) evs.push(Telemetry.sanitize({ type: "generate", scene: "痛点放大", success: true, qcPass: true, paramsVersion: 1, skillVersion: 1, anonId: "x", ts: 1 }));
  for (let i = 0; i < badN; i++) evs.push(Telemetry.sanitize({ type: "generate", scene: "反常识", success: i % 5 !== 0, qcPass: false, paramsVersion: 1, skillVersion: 1, anonId: "x", ts: 1 }));
  return evs;
}

console.log("evolution.test.ts");

test("SkillParams: clamp 权重到 [0,2]", () => {
  const c = clampParams({ ...DEFAULT_PARAMS, sceneWeights: { a: 5, b: -1 }, qualityThreshold: 999 });
  assert.strictEqual(c.sceneWeights.a, 2);
  assert.strictEqual(c.sceneWeights.b, 0);
  assert.strictEqual(c.qualityThreshold, 115);
});

test("SkillParams: 学习率限制每轮漂移 + 版本+1", () => {
  const cur = { ...DEFAULT_PARAMS, sceneWeights: { 痛点放大: 1 } };
  const cand = { ...cur, sceneWeights: { 痛点放大: 2 } };
  const next = applyLearningRate(cur, cand, 0.3);
  assert.ok(Math.abs(next.sceneWeights["痛点放大"] - 1.3) < 1e-6, "1 + 0.3*(2-1)=1.3");
  assert.strictEqual(next.version, cur.version + 1);
});

test("Telemetry: 关闭时不记录；脱敏剔除非白名单(PII)", () => {
  const off = new Telemetry(false);
  off.record({ type: "generate", success: true, skillVersion: 1, paramsVersion: 1 });
  assert.strictEqual(off.drain().length, 0);
  const clean = Telemetry.sanitize({ type: "generate", success: true, anonId: "x", ts: 1, productName: "secret", userEmail: "a@b.com" } as never);
  assert.ok(!("productName" in clean) && !("userEmail" in clean), "PII 被剔除");
});

test("QualitySignals: 全成功 → 高分，分在 [0,1]", () => {
  const q = computeQuality(synth(40, 0));
  assert.ok(q.score > 0.8 && q.score <= 1);
  assert.strictEqual(q.successRate, 1);
});

test("EvolutionEngine: 样本足够 → 采纳，好角度↑差角度↓", () => {
  const eng = new EvolutionEngine();
  const before = eng.getParams().sceneWeights["痛点放大"];
  const round = eng.evolveRound(synth(40, 40));
  assert.strictEqual(round.accepted, true);
  assert.ok(eng.getParams().sceneWeights["痛点放大"] > before, "好角度权重上升");
  assert.ok(eng.getParams().sceneWeights["反常识"] < 1, "差角度权重下降");
});

test("EvolutionEngine: 样本不足 → 门控拒绝", () => {
  const eng = new EvolutionEngine();
  const round = eng.evolveRound(synth(5, 5));
  assert.strictEqual(round.accepted, false);
  assert.match(round.reason, /样本不足/);
});

test("EvolutionEngine: 回滚恢复 last-good", () => {
  const eng = new EvolutionEngine();
  eng.evolveRound(synth(40, 40)); // 采纳一次
  const restored = eng.rollback();
  assert.strictEqual(restored.sceneWeights["痛点放大"], 1, "回滚到初始");
});

test("EvolutionEngine.validate: 不得给零质量角度加权", () => {
  const eng = new EvolutionEngine();
  const evs = synth(40, 0).concat(
    Array.from({ length: 10 }, () => Telemetry.sanitize({ type: "generate", scene: "反常识", success: false, qcPass: false, paramsVersion: 1, skillVersion: 1, anonId: "x", ts: 1 })),
  );
  const bad = { ...eng.getParams(), sceneWeights: { ...eng.getParams().sceneWeights, 反常识: 1.8 } };
  assert.strictEqual(eng.validate(bad, evs).ok, false);
});

console.log(`\n全部通过：${passed} 个用例 ✓`);
