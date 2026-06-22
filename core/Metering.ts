/**
 * Metering.ts — 用量与成本计量（按租户），支撑计费/对账。
 * 内存实现；生产可把 record 落库或推到计费系统。
 */
export interface UsageRecord {
  tenantId: string;
  jobId: string;
  costUsd: number;
  variants: number;
  ts: number;
}

export class Metering {
  private records: UsageRecord[] = [];
  record(r: UsageRecord): void {
    this.records.push(r);
  }
  summary(tenantId: string): { jobs: number; variants: number; totalCostUsd: number } {
    const rs = this.records.filter((r) => r.tenantId === tenantId);
    return {
      jobs: rs.length,
      variants: rs.reduce((s, r) => s + r.variants, 0),
      totalCostUsd: +rs.reduce((s, r) => s + r.costUsd, 0).toFixed(4),
    };
  }
  all(): UsageRecord[] {
    return [...this.records];
  }
}
