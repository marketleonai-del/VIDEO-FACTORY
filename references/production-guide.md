# 生产部署 & 大规模使用指南

> 目标：从"能跑"到"很多人能稳定大规模用"。本指南覆盖密钥、扩展、并发、可靠性、成本、可观测、安全。

## 1. 配置与密钥
- 复制 `.env.example` → `.env`，填入各家 Key/端点（缺失项对应适配器自动走 demo）。
- 代码侧用 `createEngineFromEnv()`（自动 `loadDotenvFile()` + 读环境变量）。
- **永不把密钥写进代码或日志**；容器里用环境变量/密钥管理（K8s Secret、SSM、Vault）。

## 2. 两种接入形态
| 形态 | 启动 | 适合 |
|---|---|---|
| CLI | `npm run build && node dist/bin/cli.js generate --count 20 …` | 个人/脚本/定时任务 |
| HTTP server | `node dist/server.js`（`PORT` 可配） | 多用户/前端/服务化 |

API：`POST /generate`(异步建任务→返回 jobId) · `GET /jobs/:id`(查状态/结果) · `GET /jobs` · `GET /health`。

## 3. 大规模并发
- **批量**走 `BatchExecutor`：受控并发(`pLimit`) + 逐条重试 + JobStore 追踪 + 成本/质检汇总。
- 并发度 `UVG_CONCURRENCY`（默认 4）。按各家**速率上限**调；视频模型多有 QPS/并发限制。
- 每个 provider 可包 `RateLimiter(minIntervalMs)` 控制最小调用间隔，防限频/封号。

## 4. 水平扩展（多副本）
- server 本身**无状态**（除内存 JobStore）。要多副本，需把 `MemoryJobStore` 换成**共享后端**：实现同一个 `IJobStore` 接口的 Redis/Postgres 版即可，核心零改动。
- 重活（视频生成）建议丢**消息队列**（如 Redis/SQS）由 worker 消费，server 只入队 → 天然横向扩展。
- 产物（音频/视频）存对象存储（S3/OSS/Cloudinary），`audioUrl/videoUrl` 存可访问 URL。

## 5. 可靠性
- **重试退避**：`withRetry`（指数退避，默认 3 次）。
- **回退**：选型返回 `[首选,回退…]`，`generateWithFallback` 首选失败自动换下一个模型。
- **轮询**：异步任务用 `pollUntil`（超时保护）。
- **健康检查**：`/health` 或 `cli health` 看各适配器是否就绪。

## 6. 成本控制
- 默认策略：AI 只做 3s 钩子 + 真素材拼接（省 ~90%）；声纹克隆一次性、全矩阵复用。
- `CostEstimator.estimateMatrix` 跑批前先估总价；按预算档（minimal/standard/premium）自动降/升级。
- 优先本地/开源（Wan / CosyVoice / GPT-SoVITS，边际成本≈0）压成本。

## 7. 真实渲染（ffmpeg）
- 安装 ffmpeg（系统二进制，非 npm 依赖），`FFMPEG_BIN` 指定路径。
- `FFmpegAssembler` 把"全静音片段 + 单一 @voice1 音轨"拼成 mp4；缺 ffmpeg 优雅降级为输出命令计划。
- 远程 URL 需先下载为本地文件再渲染（生产中由下载步骤完成）。

## 8. 可观测 & 安全
- 日志 `Logger`（`UVG_LOG_LEVEL`），生产用 `info`/`warn`；接 ELK/Datadog 可换 transport。
- 入口校验 `validateWorkflowInput`；server 限制 body 大小；前置网关做鉴权/限流/配额。
- 给每用户/租户配额与并发上限，避免单租户打爆下游模型额度。

## 9. 上线检查清单
- [ ] `.env` 已配真实 Key；`/health` 各适配器 ready
- [ ] JobStore 换共享后端（多副本时）
- [ ] 队列 + worker（高吞吐时）
- [ ] 对象存储托管产物
- [ ] ffmpeg 已装、`FFMPEG_BIN` 正确
- [ ] 速率限制 + 每租户配额
- [ ] 监控/告警 + 结构化日志
- [ ] 合规：AI 生成标识 + 各平台/地区披露（继承 ugc-creative-amplifier 合规层）

## 10. 商用运维（v2.0）
- **多租户**：`UVG_TENANTS` 配置各租户 apiKey 与配额；`/v1/*` 按 tenantId 隔离任务与用量。
- **配额/计费**：`Quota` 控并发/日额/成本上限；`Metering` + `/v1/usage` 出量与成本，对接计费系统。
- **幂等**：客户端带 `Idempotency-Key`，重试不重复扣费/生成。
- **韧性**：熔断器隔离故障下游；超时 + 重试退避 + 回退；`/ready` 做就绪门控、`/health` 探活。
- **可观测**：结构化 JSON 日志（含 reqId/tenant）；`/metrics` 指标；接 ELK/Datadog/Prometheus。
- **部署**：`docker compose up`（运行期零依赖镜像 + ffmpeg + 健康检查）；CI 跑 typecheck+test+build。
- **扩展到大规模**：`FileJobStore`→Redis/DB（实现 `IJobStore`）；队列→外部 MQ + worker；产物→S3/OSS（实现 `Storage`）；server 无状态多副本 + 负载均衡。
- **安全**：密钥进 Secret Manager；网关层 WAF/限流/配额；定期安全评审与依赖审计。
