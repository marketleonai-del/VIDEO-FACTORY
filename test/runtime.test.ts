/**
 * runtime.test.ts — 生产运行时 + 批量 + 校验 + 组装 单元测试（零依赖）
 * 运行：npx ts-node test/runtime.test.ts
 */
import assert from "node:assert";
import { withRetry, pLimit, RateLimiter, sleep } from "../core/runtime";
import { validateWorkflowInput } from "../core/Config";
import { BatchExecutor } from "../core/BatchExecutor";
import { FFmpegAssembler } from "../core/FFmpegAssembler";
import { MemoryJobStore } from "../core/JobStore";
import { createEngine } from "../index";
import { WorkflowInput } from "../workflows/WorkflowContext";

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  await fn();
  passed++;
  console.log(`  ✓ ${name}`);
}

async function main(): Promise<void> {
  console.log("runtime.test.ts");

  await test("withRetry: 前两次失败后成功", async () => {
    let n = 0;
    const v = await withRetry(
      async () => {
        n++;
        if (n < 3) throw new Error("fail");
        return "ok";
      },
      { retries: 3, baseDelayMs: 1 },
    );
    assert.strictEqual(v, "ok");
    assert.strictEqual(n, 3);
  });

  await test("withRetry: 用尽后抛错", async () => {
    await assert.rejects(() => withRetry(async () => { throw new Error("nope"); }, { retries: 1, baseDelayMs: 1 }));
  });

  await test("pLimit: 并发不超过上限", async () => {
    const limit = pLimit(2);
    let active = 0, max = 0;
    await Promise.all(
      Array.from({ length: 6 }, () =>
        limit(async () => {
          active++; max = Math.max(max, active);
          await sleep(5); active--;
        }),
      ),
    );
    assert.ok(max <= 2, `max concurrency ${max} <= 2`);
  });

  await test("RateLimiter: 调用被拉开间隔", async () => {
    const rl = new RateLimiter(20);
    const t0 = Date.now();
    await rl.schedule(async () => 1);
    await rl.schedule(async () => 2);
    assert.ok(Date.now() - t0 >= 18, "两次调用间隔 >= ~20ms");
  });

  await test("validateWorkflowInput: 捕获非法输入", () => {
    const errs = validateWorkflowInput({ mode: "x" as never, matrixCount: 0 } as never);
    assert.ok(errs.length >= 3);
  });

  await test("BatchExecutor: 并发跑 5 条全部成功（demo）", async () => {
    const { managers, builder } = createEngine({ includeSkeletons: false });
    const base: WorkflowInput = {
      mode: "from-scratch", product: "x", hasRealPersonAudio: false, hasOwnMaterials: false,
      budgetTier: "standard", platform: "douyin", matrixCount: 1, language: "zh", durationSec: 30, aspectRatio: "9:16",
    };
    const store = new MemoryJobStore<WorkflowInput, unknown>();
    const be = new BatchExecutor(managers.models, managers.tts, builder);
    const s = await be.run(base, 5, { concurrency: 3, jobStore: store as never });
    assert.strictEqual(s.total, 5);
    assert.strictEqual(s.succeeded, 5);
    assert.strictEqual(store.list().length, 5);
  });

  await test("FFmpegAssembler.buildPlan: 生成 concat + 音轨命令", () => {
    const plan = new FFmpegAssembler().buildPlan(
      { order: [{ kind: "ai-hook", url: "a.mp4", muted: true }, { kind: "real", url: "b.mp4", muted: true }], audioTrack: ["v.wav"] },
      "out.mp4",
    );
    assert.ok(plan.command.includes("-filter_complex"));
    assert.ok(plan.command.join(" ").includes("concat=n=2"));
    assert.ok(plan.inputs.length === 2);
  });

  console.log(`\n全部通过：${passed} 个用例 ✓`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
