/**
 * Auth.ts — 多租户 + API Key 鉴权（商用基础）
 * 租户来自环境变量；每个租户带独立配额。生产可换成 DB/控制台下发。
 */
export interface TenantQuota {
  maxConcurrent: number;
  dailyMax: number;
  dailyCostCapUsd: number;
}
export const DEFAULT_QUOTA: TenantQuota = { maxConcurrent: 4, dailyMax: 1000, dailyCostCapUsd: 100 };

export interface Tenant {
  id: string;
  name: string;
  apiKey: string;
  plan?: string;
  quota?: Partial<TenantQuota>;
}

export class TenantRegistry {
  private byKey = new Map<string, Tenant>();
  constructor(tenants: Tenant[] = []) {
    tenants.forEach((t) => this.add(t));
  }
  add(t: Tenant): void {
    this.byKey.set(t.apiKey, t);
  }
  /** 解析 Authorization 头（Bearer <key> 或裸 key）→ 租户 */
  authenticate(authHeader?: string): Tenant | undefined {
    if (!authHeader) return undefined;
    const m = /^Bearer\s+(.+)$/i.exec(authHeader.trim());
    const key = m ? m[1] : authHeader.trim();
    return this.byKey.get(key);
  }
  list(): Array<{ id: string; name: string; plan?: string }> {
    return [...this.byKey.values()].map((t) => ({ id: t.id, name: t.name, plan: t.plan }));
  }
  size(): number {
    return this.byKey.size;
  }
}

/**
 * 从环境变量构建租户表：
 *   UVG_TENANTS='[{"id":"acme","name":"Acme","apiKey":"sk_...","quota":{"maxConcurrent":8}}]'
 *   UVG_DEV_KEY=sk_dev  → 追加一个 dev 租户（仅开发用）
 */
export function tenantsFromEnv(env: NodeJS.ProcessEnv = process.env): TenantRegistry {
  const reg = new TenantRegistry();
  if (env.UVG_TENANTS) {
    try {
      (JSON.parse(env.UVG_TENANTS) as Tenant[]).forEach((t) => reg.add(t));
    } catch {
      /* 忽略非法 JSON */
    }
  }
  if (env.UVG_DEV_KEY) reg.add({ id: "dev", name: "dev", apiKey: env.UVG_DEV_KEY, plan: "dev" });
  return reg;
}
