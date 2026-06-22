/**
 * BatchExecutor.ts — 大规模批量执行（矩阵）
 * 把 1 个基准输入扩成 N 条变体，受控并发跑完，逐条重试，统一到 JobStore，汇总成本/质检。
 * 这是"大规模使用"的核心：并发 + 重试 + 任务追踪 + 成本与质量汇总。
 */
import { pLimit, withRetry, logger } from "./runtime";
import { IJobStore, MemoryJobStore } from "./JobStore";
import { ModelRegistry } from "./ModelRegistry";
import { TTSRegistry } from "./TTSRegistry";
import { VoiceLockManager } from "./VoiceLockManager";
import { CostEstimator } from "./CostEstimator";
import { QualityGate } from "./QualityGate";
import { WorkflowBuilder } from "../workflows/WorkflowBuilder";
import { createContext, WorkflowInput, WorkflowManagers } from "../workflows/WorkflowContext";

export interface BatchOptions {
  concurrency?: number;
  retries?: number;
  jobStore?: IJobStore<WorkflowInput, BatchVariantResult>;
}
export interface BatchVariantResult {
  index: number;
  ok: boolean;
  qualityScore?: number;
  qualityPass?: boolean;
  costUsd?: number;
  trace?: string[];
  error?: string;
}
export interface BatchSummary {
  total: number;
  succeeded: number;
  failed: number;
  allQualityPass: boolean;
  totalCostUsd: number;
  results: BatchVariantResult[];
  jobIds: string[];
}

/** 为矩阵创建一份"每变体独享"的管理器（声纹锁/状态不串） */
function freshManagers(shared: { models: ModelRegistry; tts: TTSRegistry }): WorkflowManagers {
  return {
    models: shared.models,
    tts: shared.tts,
    voiceLock: new VoiceLockManager(shared.tts), // 每条独立声纹状态
    cost: new CostEstimator(shared.models, shared.tts),
    quality: new QualityGate(),
  };
}

export class BatchExecutor {
  constructor(private models: ModelRegistry, private tts: TTSRegistry, private builder: WorkflowBuilder) {}

  /** 把基准输入扩成 N 条（每条 matrixCount=1，独立生成；差异化由 matrix 语义体现） */
  expand(base: WorkflowInput, n: number): WorkflowInput[] {
    return Array.from({ length: n }, () => ({ ...base, matrixCount: 1 }));
  }

  /** 受控并发执行整个矩阵 */
  async run(base: WorkflowInput, n: number, opts: BatchOptions = {}): Promise<BatchSummary> {
    const concurrency = opts.concurrency ?? 4;
    const retries = opts.retries ?? 2;
    const store = opts.jobStore ?? new MemoryJobStore<WorkflowInput, BatchVariantResult>();
    const limit = pLimit(concurrency);
    const variants = this.expand(base, n);
    logger.info(`批量启动：${n} 条，并发 ${concurrency}`);

    const tasks = variants.map((input, i) =>
      limit(async () => {
        const job = store.create(input);
        store.update(job.id, { state: "running" });
        try {
          const res = await withRetry(
            async () => {
              const ctx = createContext(input, freshManagers({ models: this.models, tts: this.tts }));
              return this.builder.build(ctx).run(ctx);
            },
            { retries, onRetry: (a, e) => logger.warn(`变体#${i + 1} 第${a}次重试：${e.message}`) },
          );
          const q = res.artifacts.qualityResults?.[0];
          const out: BatchVariantResult = {
            index: i + 1,
            ok: true,
            qualityScore: q?.score,
            qualityPass: q?.pass,
            costUsd: res.artifacts.costReport?.totalUsd,
            trace: res.trace,
          };
          store.update(job.id, { state: "succeeded", result: out, progress: 100 });
          return { out, jobId: job.id };
        } catch (e) {
          const out: BatchVariantResult = { index: i + 1, ok: false, error: (e as Error).message };
          store.update(job.id, { state: "failed", error: out.error, result: out });
          return { out, jobId: job.id };
        }
      }),
    );

    const settled = await Promise.all(tasks);
    const results = settled.map((s) => s.out).sort((a, b) => a.index - b.index);
    const succeeded = results.filter((r) => r.ok).length;
    return {
      total: n,
      succeeded,
      failed: n - succeeded,
      allQualityPass: results.every((r) => r.qualityPass !== false),
      totalCostUsd: +results.reduce((s, r) => s + (r.costUsd ?? 0), 0).toFixed(4),
      results,
      jobIds: settled.map((s) => s.jobId),
    };
  }
}
