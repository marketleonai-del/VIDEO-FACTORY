# HTTP API（v1）

鉴权：所有 `/v1/*` 需 `Authorization: Bearer <apiKey>`（租户由 `UVG_TENANTS` / `UVG_DEV_KEY` 配置）。
返回：JSON；错误为 `{ "error": { "code", "message", "details? } }` + 对应 HTTP 状态码。
关联：每个响应带 `x-request-id`，日志按该 ID 串联。

## 公共端点（无需鉴权）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活探针 → `{ ok: true }` |
| GET | `/ready` | 就绪探针 → 各适配器可用性 + 租户数；未就绪 503 |
| GET | `/metrics` | 计数器/计时器快照（接入 Prometheus 可改造） |

## 业务端点
### POST /v1/generate
入参（body = `WorkflowInput` + 可选 `concurrency`/`callbackUrl`）：
```json
{
  "mode": "from-winner",
  "matrixCount": 20,
  "budgetTier": "standard",
  "platform": "douyin",
  "hasRealPersonAudio": true,
  "realPersonAudioSample": "https://.../voice.wav",
  "hasOwnMaterials": true,
  "materials": [{ "id": "R1", "type": "口播", "url": "https://.../r1.mp4", "trustValue": "high" }],
  "language": "zh", "durationSec": 30, "aspectRatio": "9:16",
  "callbackUrl": "https://your.app/webhook"
}
```
- 头 `Idempotency-Key: <key>`（可选）：重复请求返回同一 `jobId`，不重复执行/计费。
- 受配额限制：超并发/日额/成本上限 → `429 quota_exceeded`。
- 返回 `202 { "jobId", "state": "queued", "dedup" }`。

### GET /v1/jobs/:id
查任务（仅本租户）：`{ id, state, result?, error?, progress, ... }`。state ∈ queued/running/succeeded/failed。

### GET /v1/jobs
本租户任务列表（摘要）。

### GET /v1/usage
本租户当日配额用量 + 累计计量（jobs/variants/totalCostUsd）。

## Webhook
若提交 `callbackUrl`，任务完成时服务端 `POST` 该地址：
```json
{ "jobId": "...", "state": "succeeded", "result": { "kind": "matrix", "costUsd": 6, "data": { ... } } }
```

## curl 示例
```bash
# 提交（幂等）
curl -XPOST localhost:8787/v1/generate \
  -H "Authorization: Bearer sk_dev_local" -H "Idempotency-Key: req-123" \
  -H "Content-Type: application/json" \
  -d '{"mode":"from-scratch","product":"便携榨汁杯","matrixCount":10,"budgetTier":"standard","platform":"douyin","hasRealPersonAudio":false,"hasOwnMaterials":false,"language":"zh","durationSec":30,"aspectRatio":"9:16"}'
# 查询
curl localhost:8787/v1/jobs/<jobId> -H "Authorization: Bearer sk_dev_local"
# 用量
curl localhost:8787/v1/usage -H "Authorization: Bearer sk_dev_local"
```
