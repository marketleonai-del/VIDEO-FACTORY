/**
 * commercial.test.ts — 商用层单测：鉴权/配额/计量/持久化/幂等队列/熔断/超时
 */
import assert from "node:assert";
import * as fs from "node:fs";
import { TenantRegistry } from "../core/Auth";
import { QuotaManager } from "../core/Quota";
import { Metering } from "../core/Metering";
import { FileJobStore } from "../core/FileJobStore";
import { JobQueue } from "../core/Queue";
import { CircuitBreaker, withTimeout } from "../core/resilience";
import { sleep } from "../core/runtime";

let passed = 0;
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  await fn();
  passed++;
  console.log(`  ✓ ${name}`);
}

async function main(): Promise<void> {
  console.log("commercial.test.ts");

  await test("Auth: Bearer 鉴权解析", () => {
    const reg = new TenantRegistry([{ id: "a", name: "A", apiKey: "k1" }]);
    assert.strictEqual(reg.authenticate("Bearer k1")?.id, "a");
    assert.strictEqual(reg.authenticate("Bearer bad"), undefined);
    assert.strictEqual(reg.authenticate(undefined), undefined);
  });

  await test("Quota: 并发上限 + 释放", () => {
    const q = new QuotaManager();
    const t = { id: "t", name: "T", apiKey: "k", quota: { maxConcurrent: 1 } };
    assert.strictEqual(q.tryAcquire(t).ok, true);
    assert.strictEqual(q.tryAcquire(t).ok, false); // 并发满
    q.release("t");
    assert.strictEqual(q.tryAcquire(t).ok, true);
  });

  await test("Quota: 日成本上限", () => {
    const q = new QuotaManager();
    const t = { id: "t2", name: "T", apiKey: "k", quota: { dailyCostCapUsd: 1 } };
    assert.strictEqual(q.tryAcquire(t).ok, true);
    q.recordCost("t2", 2);
    assert.strictEqual(q.tryAcquire(t).ok, false); // 超成本
  });

  await test("Metering: 用量汇总", () => {
    const m = new Metering();
    m.record({ tenantId: "t", jobId: "j1", costUsd: 1.5, variants: 10, ts: Date.now() });
    m.record({ tenantId: "t", jobId: "j2", costUsd: 0.5, variants: 5, ts: Date.now() });
    const s = m.summary("t");
    assert.strictEqual(s.jobs, 2);
    assert.strictEqual(s.variants, 15);
    assert.strictEqual(s.totalCostUsd, 2);
  });

  await test("FileJobStore: 落盘 + 重载持久化", () => {
    const f = `/tmp/uvg-test-${Date.now()}.json`;
    const s1 = new FileJobStore<{ x: number }, unknown>(f);
    const job = s1.create({ x: 1 });
    s1.update(job.id, { state: "succeeded" });
    const s2 = new FileJobStore<{ x: number }, unknown>(f); // 新实例从磁盘加载
    assert.strictEqual(s2.get(job.id)?.state, "succeeded");
    fs.unlinkSync(f);
  });

  await test("JobQueue: 幂等键去重 + 执行", async () => {
    const f = `/tmp/uvg-q-${Date.now()}.json`;
    const store = new FileJobStore<{ n: number }, number>(f);
    const q = new JobQueue<{ n: number }, number>(store, async (i) => i.n * 2, { concurrency: 2 });
    const a = q.enqueue({ n: 21 }, "same");
    const b = q.enqueue({ n: 99 }, "same"); // 幂等命中
    assert.strictEqual(a.jobId, b.jobId);
    assert.strictEqual(b.dedup, true);
    for (let i = 0; i < 20 && store.get(a.jobId)?.state !== "succeeded"; i++) await sleep(20);
    assert.strictEqual(store.get(a.jobId)?.result, 42);
    fs.unlinkSync(f);
  });

  await test("CircuitBreaker: 连续失败后熔断打开", async () => {
    const cb = new CircuitBreaker("t", { failureThreshold: 2, cooldownMs: 5000 });
    await assert.rejects(cb.exec(async () => { throw new Error("x"); }));
    await assert.rejects(cb.exec(async () => { throw new Error("x"); }));
    assert.strictEqual(cb.currentState, "open");
    await assert.rejects(cb.exec(async () => 1), /open/); // 已熔断，快速失败
  });

  await test("withTimeout: 超时拒绝", async () => {
    await assert.rejects(withTimeout(new Promise(() => undefined), 10, "op"), /超时/);
  });

  console.log(`\n全部通过：${passed} 个用例 ✓`);
}
main().catch((e) => { console.error(e); process.exit(1); });
