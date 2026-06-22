# 商用就绪报告 · universal-video-generator v2.0

> 验证：全量 `tsc --noEmit` 0 错误；单测 core/runtime/commercial/server 全通过；server 集成测试跑通；demo 离线可跑。
> 结论：**已具备对外商用的工程骨架（多租户 SaaS 形态）**；接真实 Key/装 ffmpeg/换共享后端即可上线。

## 商用支柱核对
| 支柱 | 状态 | 实现 |
|---|---|---|
| 多租户鉴权 | ✅ | `Auth`(API Key→租户) + server 鉴权中间件 + 租户隔离（任务按 tenantId 隔离） |
| 配额 & 限流 | ✅ | `Quota`(并发/日额/日成本上限，自然日重置) → 超限 429 |
| 用量计量/计费 | ✅ | `Metering`(按租户 jobs/variants/cost) + `/v1/usage`；可对接计费系统 |
| 幂等 | ✅ | `Idempotency-Key` → 同 jobId，不重复执行/计费 |
| 持久化任务 | ✅ | `FileJobStore` 落盘，重启不丢；接口化可换 Redis/DB |
| 并发队列 | ✅ | `JobQueue`(并发受限 + 幂等) |
| 韧性 | ✅ | 重试退避 + 并发限制 + 速率限制 + **熔断器** + **超时** + 回退 |
| 可观测 | ✅ | 结构化 JSON 日志 + `/metrics` 指标 + 请求 ID 关联 + `/health` `/ready` |
| Webhook | ✅ | 任务完成回调 `callbackUrl` |
| 安全 | ✅ | Bearer 鉴权 / body 限长 / CORS / 类型化错误 / 不持久化 apiKey（任务存 tenantId） |
| 优雅关停 | ✅ | SIGTERM/SIGINT → drain + 退出 |
| 产物存储 | ✅(本地) | `Storage` 抽象 + `LocalStorage`；生产换 S3/OSS 实现同接口 |
| DevOps | ✅ | Dockerfile(多阶段,运行期零依赖)+compose+健康检查+GitHub Actions CI |
| API 契约 | ✅ | `API.md`（端点/鉴权/错误/幂等/Webhook/示例） |

## 上线前必接（环境/外部依赖，非代码缺陷）
- 🔑 各模型/TTS **真实 API Key**（填 `.env`；缺则 demo）。端点字段以官网为准（搜 `// VERIFY`）。
- 🎞 **ffmpeg**（Docker 已装；裸机自行安装）做真实合成；远程产物先下载再渲染。
- 🗄 **多副本横向扩展**：把 `FileJobStore` 换 Redis/Postgres 实现（同 `IJobStore` 接口）；队列换外部 MQ + 独立 worker。
- 🔐 **密钥管理**：生产用 Secret Manager/Vault，勿提交 `.env`。
- 📈 **可观测后端**：日志接 ELK/Datadog；`/metrics` 接 Prometheus（改 exposition 格式）。
- 🧩 **P1/P2 适配器**：能力画像就绪，`doGenerate/synthesize` 按各家 API 补全（搜 `ADAPTER_NOT_IMPLEMENTED`）。
- 🛡 **安全合规**：上线前做一次安全评审（鉴权/越权/注入/速率）；内容合规标识沿用 `ugc-creative-amplifier` 合规层；上游网关做 WAF/限流/配额二次防护。
- ⚖️ **SLA/容量**：按各模型 QPS 配并发与熔断阈值；做容量与成本压测。

## 不在范围（按需再加）
- 计费对账落地（对接 Stripe/支付）、控制台 UI、用户/团队管理、审计日志留存策略、灰度与多区域部署。
