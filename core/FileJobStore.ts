/**
 * FileJobStore.ts — 落盘版任务存储（重启不丢任务），实现 IJobStore。
 * 单实例够用；多副本请实现同接口的 Redis/Postgres 版（见 production-guide）。
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { IJobStore, Job } from "./JobStore";

export class FileJobStore<I = unknown, R = unknown> implements IJobStore<I, R> {
  private jobs = new Map<string, Job<I, R>>();
  private seq = 0;
  constructor(private file = process.env.UVG_JOBS_FILE || "./.uvg-jobs.json") {
    this.load();
  }
  private load(): void {
    try {
      if (fs.existsSync(this.file)) {
        (JSON.parse(fs.readFileSync(this.file, "utf8")) as Job<I, R>[]).forEach((j) => this.jobs.set(j.id, j));
        this.seq = this.jobs.size;
      }
    } catch {
      /* 忽略损坏的存档 */
    }
  }
  private persist(): void {
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      fs.writeFileSync(this.file, JSON.stringify([...this.jobs.values()]));
    } catch {
      /* 忽略写入错误（生产应告警） */
    }
  }
  create(input: I): Job<I, R> {
    const id = `job_${Date.now().toString(36)}_${++this.seq}`;
    const now = Date.now();
    const job: Job<I, R> = { id, state: "queued", input, createdAt: now, updatedAt: now };
    this.jobs.set(id, job);
    this.persist();
    return job;
  }
  get(id: string): Job<I, R> | undefined {
    return this.jobs.get(id);
  }
  update(id: string, patch: Partial<Job<I, R>>): Job<I, R> {
    const j = this.jobs.get(id);
    if (!j) throw new Error(`job ${id} not found`);
    const next = { ...j, ...patch, updatedAt: Date.now() };
    this.jobs.set(id, next);
    this.persist();
    return next;
  }
  list(): Job<I, R>[] {
    return [...this.jobs.values()].sort((a, b) => b.createdAt - a.createdAt);
  }
}
