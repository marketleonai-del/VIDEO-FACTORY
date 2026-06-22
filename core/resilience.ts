/**
 * resilience.ts — 韧性：超时 + 熔断器（保护下游模型，防级联失败）
 */
export async function withTimeout<T>(p: Promise<T>, ms: number, label = "operation"): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, rej) => {
    timer = setTimeout(() => rej(new Error(`${label} 超时(${ms}ms)`)), ms);
  });
  try {
    return await Promise.race([p, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

export type BreakerState = "closed" | "open" | "half-open";
export interface BreakerOptions {
  failureThreshold?: number;
  cooldownMs?: number;
  halfOpenMax?: number;
}

/** 单个下游的熔断器：连续失败→open；冷却后→half-open 试探；成功→closed */
export class CircuitBreaker {
  private state: BreakerState = "closed";
  private failures = 0;
  private openedAt = 0;
  private halfOpenInflight = 0;
  constructor(private name: string, private opts: BreakerOptions = {}) {}
  private cfg(): Required<BreakerOptions> {
    return { failureThreshold: 5, cooldownMs: 30000, halfOpenMax: 1, ...this.opts };
  }
  get currentState(): BreakerState {
    return this.state;
  }
  async exec<T>(fn: () => Promise<T>): Promise<T> {
    const c = this.cfg();
    if (this.state === "open") {
      if (Date.now() - this.openedAt >= c.cooldownMs) {
        this.state = "half-open";
        this.halfOpenInflight = 0;
      } else throw new Error(`circuit ${this.name} is open`);
    }
    if (this.state === "half-open" && this.halfOpenInflight >= c.halfOpenMax) {
      throw new Error(`circuit ${this.name} half-open busy`);
    }
    if (this.state === "half-open") this.halfOpenInflight++;
    try {
      const r = await fn();
      this.failures = 0;
      this.state = "closed";
      return r;
    } catch (e) {
      this.failures++;
      if (this.failures >= c.failureThreshold) {
        this.state = "open";
        this.openedAt = Date.now();
      }
      throw e;
    } finally {
      if (this.state === "half-open") this.halfOpenInflight = Math.max(0, this.halfOpenInflight - 1);
    }
  }
}

export class BreakerRegistry {
  private map = new Map<string, CircuitBreaker>();
  get(name: string, opts?: BreakerOptions): CircuitBreaker {
    let b = this.map.get(name);
    if (!b) {
      b = new CircuitBreaker(name, opts);
      this.map.set(name, b);
    }
    return b;
  }
  snapshot(): Array<{ name: string; state: BreakerState }> {
    return [...this.map.entries()].map(([k, v]) => ({ name: k, state: v.currentState }));
  }
}
