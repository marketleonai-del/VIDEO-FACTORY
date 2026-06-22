/**
 * server.test.ts — 商用 server 集成测试（启服务→鉴权→生成→查询→用量）
 */
import assert from "node:assert";
import * as http from "node:http";
import { once } from "node:events";

process.env.UVG_DEV_KEY = "sk_test";
process.env.PORT = "0"; // 临时端口
process.env.UVG_LOG_LEVEL = "silent";
process.env.UVG_INCLUDE_SKELETONS = "false";
process.env.UVG_JOBS_FILE = `/tmp/uvg-srv-${Date.now()}.json`;

interface Resp { status: number; json: any }
function call(port: number, method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<Resp> {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : undefined;
    const req = http.request(
      { host: "127.0.0.1", port, method, path, headers: { "Content-Type": "application/json", ...headers } },
      (res) => {
        let b = "";
        res.on("data", (c) => (b += c));
        res.on("end", () => resolve({ status: res.statusCode ?? 0, json: b ? JSON.parse(b) : null }));
      },
    );
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

async function main(): Promise<void> {
  console.log("server.test.ts");
  const mod = await import("../server");
  const server = mod.server;
  if (!server.listening) await once(server, "listening");
  const addr = server.address();
  const port = addr && typeof addr === "object" ? addr.port : 0;
  let passed = 0;
  const ok = (n: string): void => { passed++; console.log(`  ✓ ${n}`); };

  let r = await call(port, "GET", "/health");
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.json.ok, true);
  ok("/health 200");

  r = await call(port, "GET", "/ready");
  assert.ok(r.json.models.length > 0);
  ok("/ready 列出适配器");

  r = await call(port, "POST", "/v1/generate", { mode: "from-scratch" });
  assert.strictEqual(r.status, 401);
  ok("无鉴权 → 401");

  const wf = { mode: "from-scratch", product: "x", matrixCount: 1, budgetTier: "standard", platform: "douyin", hasRealPersonAudio: false, hasOwnMaterials: false, language: "zh", durationSec: 30, aspectRatio: "9:16" };
  r = await call(port, "POST", "/v1/generate", wf, { authorization: "Bearer sk_test", "idempotency-key": "k1" });
  assert.strictEqual(r.status, 202);
  const jobId = r.json.jobId;
  ok("鉴权 + 提交 → 202 jobId");

  r = await call(port, "POST", "/v1/generate", wf, { authorization: "Bearer sk_test", "idempotency-key": "k1" });
  assert.strictEqual(r.json.dedup, true);
  ok("幂等键 → dedup");

  let job: any;
  for (let i = 0; i < 40; i++) {
    r = await call(port, "GET", `/v1/jobs/${jobId}`, undefined, { authorization: "Bearer sk_test" });
    job = r.json;
    if (job.state === "succeeded" || job.state === "failed") break;
    await sleep(50);
  }
  assert.strictEqual(job.state, "succeeded");
  ok("任务完成 succeeded");

  r = await call(port, "GET", "/v1/usage", undefined, { authorization: "Bearer sk_test" });
  assert.strictEqual(r.status, 200);
  assert.ok(r.json.metering.jobs >= 1);
  ok("/v1/usage 计量");

  r = await call(port, "GET", "/v1/jobs/nonexist", undefined, { authorization: "Bearer sk_test" });
  assert.strictEqual(r.status, 404);
  ok("越权/不存在 → 404");

  server.close();
  console.log(`\n全部通过：${passed} 个用例 ✓`);
}
main().catch((e) => { console.error(e); process.exit(1); });
