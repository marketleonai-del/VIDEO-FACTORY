/**
 * server.ts — 商用 HTTP 服务（node:http，零依赖）
 * 能力：多租户鉴权 + 配额 + 幂等 + 持久任务队列 + Webhook 回调 + 结构化日志 + 指标 +
 *       /health /ready /metrics + 租户隔离 + CORS + 类型化错误 + 优雅关停。
 * 启动：node dist/server.js（PORT 默认 8787）。鉴权：Authorization: Bearer <apiKey>。
 */
import * as http from "node:http";
import { createEngineFromEnv, Engine } from "./index";
import { FileJobStore } from "./core/FileJobStore";
import { JobQueue } from "./core/Queue";
import { BatchExecutor } from "./core/BatchExecutor";
import { createContext, WorkflowInput } from "./workflows/WorkflowContext";
import { validateWorkflowInput } from "./core/Config";
import { tenantsFromEnv } from "./core/Auth";
import { QuotaManager } from "./core/Quota";
import { Metering } from "./core/Metering";
import { JsonLogger, metrics, newRequestId } from "./core/observability";
import { ApiError, Errors } from "./core/errors";

interface JobInput { wf: WorkflowInput; tenantId: string; concurrency?: number; callbackUrl?: string; }
interface JobResult { kind: "single" | "matrix"; costUsd: number; data: unknown; }

const log = new JsonLogger();
const engine: Engine = createEngineFromEnv();
const tenants = tenantsFromEnv();
const quota = new QuotaManager();
const metering = new Metering();
const store = new FileJobStore<JobInput, JobResult>();

async function notify(url: string, body: unknown): Promise<void> {
  try {
    await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (e) {
    log.warn("webhook failed", { url, err: (e as Error).message });
  }
}

const queue = new JobQueue<JobInput, JobResult>(
  store,
  async (input, job) => {
    const t0 = Date.now();
    try {
      let result: JobResult;
      if (input.wf.matrixCount > 1) {
        const be = new BatchExecutor(engine.managers.models, engine.managers.tts, engine.builder);
        const s = await be.run(input.wf, input.wf.matrixCount, { concurrency: input.concurrency ?? 4 });
        result = { kind: "matrix", costUsd: s.totalCostUsd, data: s };
      } else {
        const ctx = createContext(input.wf, engine.managers);
        const res = await engine.builder.build(ctx).run(ctx);
        result = {
          kind: "single",
          costUsd: res.artifacts.costReport?.totalUsd ?? 0,
          data: { cost: res.artifacts.costReport, quality: res.artifacts.qualityResults?.[0], manifest: res.artifacts.assemblyManifest, trace: res.trace },
        };
      }
      quota.recordCost(input.tenantId, result.costUsd);
      metering.record({ tenantId: input.tenantId, jobId: job.id, costUsd: result.costUsd, variants: input.wf.matrixCount, ts: Date.now() });
      metrics.inc("jobs.succeeded");
      metrics.observe("job.ms", Date.now() - t0);
      if (input.callbackUrl) void notify(input.callbackUrl, { jobId: job.id, state: "succeeded", result });
      return result;
    } catch (e) {
      metrics.inc("jobs.failed");
      if (input.callbackUrl) void notify(input.callbackUrl, { jobId: job.id, state: "failed", error: (e as Error).message });
      throw e;
    } finally {
      quota.release(input.tenantId);
    }
  },
  { concurrency: Number(process.env.UVG_CONCURRENCY ?? 4) },
);

function send(res: http.ServerResponse, code: number, body: unknown, reqId?: string): void {
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "authorization, content-type, idempotency-key",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    ...(reqId ? { "x-request-id": reqId } : {}),
  });
  res.end(JSON.stringify(body, null, 2));
}
function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let b = "";
    req.on("data", (c) => {
      b += c;
      if (b.length > 1_000_000) reject(Errors.badRequest("body too large (>1MB)"));
    });
    req.on("end", () => resolve(b));
    req.on("error", reject);
  });
}

const server = http.createServer(async (req, res) => {
  const reqId = newRequestId();
  const rlog = log.child({ reqId, method: req.method, path: req.url });
  metrics.inc("http.requests");
  try {
    const url = new URL(req.url ?? "/", "http://localhost");
    const path = url.pathname;
    if (req.method === "OPTIONS") return send(res, 204, {}, reqId);

    // 公共端点（无需鉴权）
    if (req.method === "GET" && path === "/health") return send(res, 200, { ok: true }, reqId);
    if (req.method === "GET" && path === "/ready") {
      const models = await Promise.all(engine.managers.models.list().map(async (m) => ({ id: m.modelId, ready: await m.healthCheck() })));
      const tts = await Promise.all(engine.managers.tts.list().map(async (p) => ({ id: p.providerId, ready: await p.healthCheck() })));
      const ready = models.length > 0 && tts.length > 0;
      return send(res, ready ? 200 : 503, { ready, models, tts, tenants: tenants.size() }, reqId);
    }
    if (req.method === "GET" && path === "/metrics") return send(res, 200, metrics.snapshot(), reqId);

    // 鉴权
    const tenant = tenants.authenticate(req.headers.authorization);
    if (!tenant) throw Errors.unauthorized();
    const tlog = rlog.child({ tenant: tenant.id });

    if (req.method === "POST" && path === "/v1/generate") {
      const raw = await readBody(req);
      let wf: WorkflowInput;
      try {
        const parsed = JSON.parse(raw) as Partial<WorkflowInput> & { concurrency?: number; callbackUrl?: string };
        wf = parsed as WorkflowInput;
      } catch {
        throw Errors.badRequest("invalid JSON");
      }
      const errs = validateWorkflowInput(wf);
      if (errs.length) throw Errors.badRequest("validation failed", errs);
      const dec = quota.tryAcquire(tenant);
      if (!dec.ok) throw Errors.quotaExceeded(dec.reason);
      const idem = (req.headers["idempotency-key"] as string) || undefined;
      const body = JSON.parse(raw) as { concurrency?: number; callbackUrl?: string };
      const { jobId, dedup } = queue.enqueue({ wf, tenantId: tenant.id, concurrency: body.concurrency, callbackUrl: body.callbackUrl }, idem);
      if (dedup) quota.release(tenant.id); // 幂等命中，未产生新工作
      metrics.inc("jobs.accepted");
      tlog.info("job accepted", { jobId, dedup });
      return send(res, 202, { jobId, state: "queued", dedup }, reqId);
    }

    if (req.method === "GET" && path === "/v1/jobs") {
      const list = store.list().filter((j) => j.input.tenantId === tenant.id);
      return send(res, 200, list.map((j) => ({ id: j.id, state: j.state, progress: j.progress, updatedAt: j.updatedAt })), reqId);
    }
    if (req.method === "GET" && path.startsWith("/v1/jobs/")) {
      const id = path.slice("/v1/jobs/".length);
      const job = store.get(id);
      if (!job || job.input.tenantId !== tenant.id) throw Errors.notFound("job not found");
      return send(res, 200, job, reqId);
    }
    if (req.method === "GET" && path === "/v1/usage") {
      return send(res, 200, { tenant: tenant.id, usage: quota.snapshot(tenant.id), metering: metering.summary(tenant.id) }, reqId);
    }

    throw Errors.notFound();
  } catch (e) {
    if (e instanceof ApiError) {
      rlog.warn("api error", { code: e.code, status: e.status });
      return send(res, e.status, e.toJSON(), reqId);
    }
    rlog.error("unhandled", { err: (e as Error).message });
    return send(res, 500, { error: { code: "internal", message: "internal error" } }, reqId);
  }
});

const PORT = Number(process.env.PORT ?? 8787);
server.listen(PORT, () => log.info("server started", { port: PORT, tenants: tenants.size() }));

function shutdown(sig: string): void {
  log.info("shutting down", { sig });
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 10000).unref();
}
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

export { server };
