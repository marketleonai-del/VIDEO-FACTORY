/**
 * longvideo.test.ts — 长视频分段规划 + 续帧锚定 + 拼接命令
 */
import assert from "node:assert";
import { LongVideoPlanner } from "../core/longvideo/LongVideoPlanner";
import { LongFormAssembler } from "../core/longvideo/LongFormAssembler";

let passed = 0;
function test(n: string, fn: () => void): void { fn(); passed++; console.log(`  ✓ ${n}`); }

console.log("longvideo.test.ts");

test("Planner: 30s/单段10s → 3段，全AI，续帧锚定", () => {
  const plan = new LongVideoPlanner().plan(30, { maxSegSec: 10, subjectAnchor: "同一主播", reanchorEvery: 3 });
  assert.strictEqual(plan.segments.length, 3);
  assert.ok(plan.segments.every((s) => s.source === "ai"));
  assert.strictEqual(plan.segments[0].continueFromIndex, undefined);
  assert.strictEqual(plan.segments[1].continueFromIndex, 0);
  assert.ok(plan.segments[0].reanchor, "首段再锚定");
});

test("Planner: 混合模式插入用户素材，AI 填缺口", () => {
  const plan = new LongVideoPlanner().plan(30, { maxSegSec: 10, userClips: [{ atSec: 10, durationSec: 5, url: "u.mp4" }] });
  const user = plan.segments.filter((s) => s.source === "user");
  assert.strictEqual(user.length, 1);
  assert.strictEqual(user[0].userClipUrl, "u.mp4");
  assert.ok(plan.segments.some((s) => s.source === "ai"));
});

test("Assembler: concat 命令含 concat=n=3", () => {
  const plan = new LongFormAssembler().buildPlan(["a.mp4", "b.mp4", "c.mp4"], "o.mp4", { transition: "hard" }, [10, 10, 10]);
  assert.ok(plan.command.join(" ").includes("concat=n=3"));
  assert.strictEqual(plan.inputs.length, 3);
});

test("Assembler: xfade 命令含 xfade", () => {
  const plan = new LongFormAssembler().buildPlan(["a.mp4", "b.mp4", "c.mp4"], "o.mp4", { transition: "xfade", xfadeDur: 0.5 }, [10, 10, 10]);
  assert.ok(plan.command.join(" ").includes("xfade"));
});

console.log(`\n全部通过：${passed} 个用例 ✓`);
