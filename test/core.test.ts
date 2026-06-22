/**
 * core.test.ts — 核心模块最小单元测试（零依赖，node:assert）
 * 运行：npx ts-node test/core.test.ts
 */
import assert from "node:assert";
import { QualityGate, PASS_LINE } from "../core/QualityGate";
import { ModelRegistry } from "../core/ModelRegistry";
import { KlingAdapter } from "../adapters/video/KlingAdapter";
import { RunwayAdapter } from "../adapters/video/RunwayAdapter";

let passed = 0;
function test(name: string, fn: () => void): void {
  fn();
  passed++;
  console.log(`  ✓ ${name}`);
}

console.log("core.test.ts");

test("QualityGate: 满分通过", () => {
  const r = new QualityGate().evaluate({ content: 30, authenticity: 25, diversity: 20, compliance: 20, loop: 15, tech: 10 });
  assert.strictEqual(r.score, 120);
  assert.strictEqual(r.pass, true);
});

test("QualityGate: 低于及格线不通过", () => {
  const r = new QualityGate().evaluate({ content: 10, authenticity: 10, diversity: 10, compliance: 10, loop: 5, tech: 5 });
  assert.ok(r.score < PASS_LINE);
  assert.strictEqual(r.pass, false);
  assert.ok(r.failures.length > 0);
});

test("ModelRegistry: 能力驱动选型（minimal 选更便宜）", () => {
  const reg = new ModelRegistry();
  reg.register(new KlingAdapter()); // $0.07/s
  reg.register(new RunwayAdapter()); // $0.05/s
  const ranked = reg.select({ mode: "text2video", aspectRatio: "9:16", durationSec: 3, budgetTier: "minimal" });
  assert.strictEqual(ranked[0].modelId, "runway"); // 更便宜者优先
});

test("ModelRegistry: 候选过滤掉不支持的画幅", () => {
  const reg = new ModelRegistry();
  reg.register(new KlingAdapter());
  const cands = reg.candidates({ mode: "text2video", aspectRatio: "21:9", durationSec: 3 });
  assert.strictEqual(cands.length, 0);
});

test("成本估算：3秒钩子远低于30秒全AI", () => {
  const kling = new KlingAdapter();
  const hook = kling.estimateCost({ mode: "text2video", prompt: { description: "x" }, durationSec: 3, resolution: "720p", aspectRatio: "9:16" });
  const full = kling.estimateCost({ mode: "text2video", prompt: { description: "x" }, durationSec: 30, resolution: "720p", aspectRatio: "9:16" });
  assert.ok(hook.totalUsd < full.totalUsd / 5);
});

console.log(`\n全部通过：${passed} 个用例 ✓`);
