/**
 * JobStore.ts — 任务存储（生成任务的状态追踪）
 * 默认内存实现，适合单实例。多实例/大规模请实现同接口的 Redis/DB 版（见 production-guide）。
 */
export type JobState = "queued" | "running" | "succeeded" | "failed";

export interface Job<I = unknown, R = unknown> {
  id: string;
  state: JobState;
  input: I;
  result?: R;
  error?: string;
  progress?: number;
  createdAt: number;
  updatedAt: number;
}

/** 任务存储契约——换后端只需实现它 */
export interface IJobStore<I = unknown, R = unknown> {
  create(input: I): Job<I, R>;
  get(id: string): Job<I, R> | undefined;
  update(id: string, patch: Partial<Job<I, R>>): Job<I, R>;
  list(): Job<I, R>[];
}

export class MemoryJobStore<I = unknown, R = unknown> implements IJobStore<I, R> {
  private jobs = new Map<string, Job<I, R>>();
  private seq = 0;

  create(input: I): Job<I, R> {
    const id = `job_${Date.now().toString(36)}_${++this.seq}`;
    const now = Date.now();
    const job: Job<I, R> = { id, state: "queued", input, createdAt: now, updatedAt: now };
    this.jobs.set(id, job);
    return job;
  }
  get(id: string): Job<I, R> | undefined {
    return this.jobs.get(id);
  }
  update(id: string, patch: Partial<Job<I, R>>): Job<I, R> {
    const j = this.jobs.get(id);
    if (!j) throw new Error(`job ${id} 不存在`);
    const next = { ...j, ...patch, updatedAt: Date.now() };
    this.jobs.set(id, next);
    return next;
  }
  list(): Job<I, R>[] {
    return [...this.jobs.values()].sort((a, b) => b.createdAt - a.createdAt);
  }
}
