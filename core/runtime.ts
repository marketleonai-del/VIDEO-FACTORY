/**
 * runtime.ts — 生产级运行时工具（零依赖）
 * 日志 + 重试退避 + 并发限制 + 速率限制 + 轮询。供适配器与批量执行器使用，支撑"大规模使用"。
 */

/* ── 日志 ── */
export type LogLevel = "silent" | "error" | "warn" | "info" | "debug";
const ORDER: Record<LogLevel, number> = { silent: 0, error: 1, warn: 2, info: 3, debug: 4 };

export class Logger {
  constructor(private level: LogLevel = "info", private prefix = "uvg") {}
  setLevel(l: LogLevel): void {
    this.level = l;
  }
  private out(l: Exclude<LogLevel, "silent">, args: unknown[]): void {
    if (ORDER[this.level] >= ORDER[l]) {
      const fn = l === "error" ? console.error : l === "warn" ? console.warn : console.log;
      fn(`[${this.prefix}:${l}]`, ...args);
    }
  }
  error(...a: unknown[]): void {
    this.out("error", a);
  }
  warn(...a: unknown[]): void {
    this.out("warn", a);
  }
  info(...a: unknown[]): void {
    this.out("info", a);
  }
  debug(...a: unknown[]): void {
    this.out("debug", a);
  }
}
/** 全局默认日志器（可被 setLevel 调整；level 也可由 UVG_LOG_LEVEL 环境变量设置） */
export const logger = new Logger((process.env.UVG_LOG_LEVEL as LogLevel) || "info");

/* ── 基础异步 ── */
export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/* ── 重试 + 指数退避 ── */
export interface RetryOptions {
  retries?: number;
  baseDelayMs?: number;
  factor?: number;
  maxDelayMs?: number;
  onRetry?: (attempt: number, err: Error) => void;
}
export async function withRetry<T>(fn: () => Promise<T>, opts: RetryOptions = {}): Promise<T> {
  const { retries = 3, baseDelayMs = 500, factor = 2, maxDelayMs = 15000, onRetry } = opts;
  let lastErr: Error | undefined;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fn();
    } catch (e) {
      lastErr = e as Error;
      if (attempt === retries) break;
      const delay = Math.min(baseDelayMs * factor ** attempt, maxDelayMs);
      onRetry?.(attempt + 1, lastErr);
      await sleep(delay);
    }
  }
  throw lastErr ?? new Error("withRetry: unknown error");
}

/* ── 并发限制（pLimit 经典实现） ── */
export function pLimit(concurrency: number): <T>(fn: () => Promise<T>) => Promise<T> {
  if (concurrency < 1) concurrency = 1;
  let active = 0;
  const queue: Array<() => void> = [];
  const next = (): void => {
    active--;
    if (queue.length) queue.shift()!();
  };
  return <T>(fn: () => Promise<T>): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      const run = (): void => {
        active++;
        fn().then(resolve, reject).finally(next);
      };
      if (active < concurrency) run();
      else queue.push(run);
    });
}

/* ── 速率限制（每个 provider 最小调用间隔，防限频/封号） ── */
export class RateLimiter {
  private last = 0;
  private chain: Promise<unknown> = Promise.resolve();
  constructor(private minIntervalMs: number) {}
  schedule<T>(fn: () => Promise<T>): Promise<T> {
    const run = async (): Promise<T> => {
      const wait = this.minIntervalMs - (Date.now() - this.last);
      if (wait > 0) await sleep(wait);
      this.last = Date.now();
      return fn();
    };
    const result = this.chain.then(run, run);
    this.chain = result.catch(() => undefined);
    return result;
  }
}

/* ── 轮询异步任务直到完成 ── */
export interface PollOptions {
  intervalMs?: number;
  timeoutMs?: number;
}
export async function pollUntil<T>(fetchOnce: () => Promise<T>, isDone: (v: T) => boolean, opts: PollOptions = {}): Promise<T> {
  const { intervalMs = 3000, timeoutMs = 300000 } = opts;
  const start = Date.now();
  for (;;) {
    const v = await fetchOnce();
    if (isDone(v)) return v;
    if (Date.now() - start > timeoutMs) throw new Error(`pollUntil 超时（${timeoutMs}ms）`);
    await sleep(intervalMs);
  }
}
