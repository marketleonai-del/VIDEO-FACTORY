/**
 * Queue.ts — 进程内并发任务队列（带幂等 + 持久 JobStore）。
 * 商用要点：幂等键防重复计费/生成；并发受限；任务可持久化。
 * 更大规模：把本队列换成外部消息队列（Redis/SQS）+ 独立 worker（见 production-guide）。
 */
import { pLimit } from "./runtime";
import { IJobStore, Job } from "./JobStore";

export interface QueueOptions {
  concurrency?: number;
}

export class JobQueue<I, R> {
  private limit: <T>(fn: () => Promise<T>) => Promise<T>;
  private idem = new Map<string, string>(); // idempotencyKey -> jobId

  constructor(
    private store: IJobStore<I, R>,
    private handler: (input: I, job: Job<I, R>) => Promise<R>,
    opts: QueueOptions = {},
  ) {
    this.limit = pLimit(opts.concurrency ?? 4);
  }

  /** 入队；提供 idempotencyKey 时重复请求返回同一 jobId（不重复执行） */
  enqueue(input: I, idempotencyKey?: string): { jobId: string; dedup: boolean } {
    if (idempotencyKey && this.idem.has(idempotencyKey)) {
      return { jobId: this.idem.get(idempotencyKey)!, dedup: true };
    }
    const job = this.store.create(input);
    if (idempotencyKey) this.idem.set(idempotencyKey, job.id);
    void this.limit(() => this.process(job.id, input));
    return { jobId: job.id, dedup: false };
  }

  private async process(id: string, input: I): Promise<void> {
    this.store.update(id, { state: "running" });
    try {
      const job = this.store.get(id)!;
      const r = await this.handler(input, job);
      this.store.update(id, { state: "succeeded", result: r, progress: 100 });
    } catch (e) {
      this.store.update(id, { state: "failed", error: (e as Error).message });
    }
  }
}
