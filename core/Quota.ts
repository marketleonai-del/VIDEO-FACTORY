/**
 * Quota.ts — 每租户配额（并发 / 日次数 / 日成本上限）。按自然日重置。
 * tryAcquire 需要 Tenant（读配额）；release/recordCost/snapshot 用 tenantId（handler 侧安全调用）。
 */
import { DEFAULT_QUOTA, Tenant, TenantQuota } from "./Auth";

interface Usage {
  date: string;
  count: number;
  costUsd: number;
  concurrent: number;
}
export type QuotaDecision = { ok: true } | { ok: false; reason: string };

export class QuotaManager {
  private usage = new Map<string, Usage>();
  private q(t: Tenant): TenantQuota {
    return { ...DEFAULT_QUOTA, ...t.quota };
  }
  private u(id: string): Usage {
    const today = new Date().toISOString().slice(0, 10);
    let x = this.usage.get(id);
    if (!x || x.date !== today) {
      x = { date: today, count: 0, costUsd: 0, concurrent: x?.concurrent ?? 0 };
      this.usage.set(id, x);
    }
    return x;
  }
  tryAcquire(t: Tenant): QuotaDecision {
    const q = this.q(t);
    const u = this.u(t.id);
    if (u.concurrent >= q.maxConcurrent) return { ok: false, reason: `concurrent limit ${q.maxConcurrent}` };
    if (u.count >= q.dailyMax) return { ok: false, reason: `daily request limit ${q.dailyMax}` };
    if (u.costUsd >= q.dailyCostCapUsd) return { ok: false, reason: `daily cost cap $${q.dailyCostCapUsd}` };
    u.concurrent++;
    u.count++;
    return { ok: true };
  }
  release(tenantId: string): void {
    const u = this.u(tenantId);
    u.concurrent = Math.max(0, u.concurrent - 1);
  }
  recordCost(tenantId: string, usd: number): void {
    const u = this.u(tenantId);
    u.costUsd = +(u.costUsd + usd).toFixed(4);
  }
  snapshot(tenantId: string): Usage {
    return { ...this.u(tenantId) };
  }
}
