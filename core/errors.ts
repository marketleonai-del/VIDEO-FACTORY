/**
 * errors.ts — 商用 API 的类型化错误契约（→ HTTP 状态码 + 机器可读 code）
 */
export type ApiErrorCode =
  | "unauthorized" | "forbidden" | "not_found" | "rate_limited"
  | "quota_exceeded" | "bad_request" | "conflict" | "internal";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: ApiErrorCode,
    message: string,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
  toJSON(): { error: { code: ApiErrorCode; message: string; details?: unknown } } {
    return { error: { code: this.code, message: this.message, details: this.details } };
  }
}

export const Errors = {
  unauthorized: (m = "missing or invalid API key"): ApiError => new ApiError(401, "unauthorized", m),
  forbidden: (m = "forbidden"): ApiError => new ApiError(403, "forbidden", m),
  notFound: (m = "not found"): ApiError => new ApiError(404, "not_found", m),
  rateLimited: (m = "rate limit exceeded"): ApiError => new ApiError(429, "rate_limited", m),
  quotaExceeded: (m = "quota exceeded"): ApiError => new ApiError(429, "quota_exceeded", m),
  badRequest: (m = "bad request", d?: unknown): ApiError => new ApiError(400, "bad_request", m, d),
  conflict: (m = "conflict"): ApiError => new ApiError(409, "conflict", m),
  internal: (m = "internal error"): ApiError => new ApiError(500, "internal", m),
};
