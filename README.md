# universal-video-generator

> 通用 AI 视频/TTS 接口层 + 动态工作流 + **长视频拼接** + **自进化** + 商用 SaaS 形态（LangChain-for-video）。统一所有视频模型与 TTS/声纹工具，上层业务不感知具体模型。是 `ugc-creative-amplifier` 的底层通用化改造。

## 安装 & 离线试跑（无需 Key）
```bash
npm install
npm run typecheck      # 类型检查（tsc 0 错）
npm test               # 46 单测：core/runtime/commercial/server/evolution/longvideo
npm run example        # 从0生成一条
npm run example:batch  # 批量并发 20 条
npm run example:long   # 长视频 60s 分段+拼接计划
npm run example:evolve # 自进化一轮（隐式遥测→验证门控→参数更新）
```

## 三大新能力
- **长视频**：超单模型上限自动多段生成 + 续帧一致（主体恒定 + 每 N 段再锚定防漂移）+ 归一化拼接（concat/xfade）+ 混合用户素材。`uvg long --duration 60 --transition xfade`。
- **自进化**：匿名 opt-in 隐式遥测（不需打分、无 PII）→ `EvolutionEngine`（bandit + 验证门控严格胜出 + 文本学习率 + 回滚）→ 越用越好；总部 `hq/` 跨用户聚合晋升/回滚。
- **商用**：多租户鉴权/配额/计量/幂等/持久/熔断/可观测/Webhook + `server.ts` + Docker/CI。

## 部署
```bash
cp .env.example .env   # 填 Key + UVG_TENANTS + (可选)UVG_TELEMETRY=on / UVG_PARAMS_URL
npm run build
node dist/server.js    # 商用 HTTP（/v1/*、/health、/ready、/metrics）
node dist/hq/collector.js   # 总部端 HQ（收集/聚合/晋升/回滚）
docker compose up      # 容器（运行期零依赖 + ffmpeg + healthcheck）
```

## 文档
- `EVOLUTION-SYSTEM-DESIGN.md`：自进化系统完整方案（8 交付物 + 风险 + 分阶段 + 可接入 skill/连接器）。
- `API.md`：HTTP 契约；`COMMERCIAL-READINESS.md`：商用就绪核对；`references/`：模型/TTS/工作流/适配器/生产指南。

## 三原则
插件式（核心不依赖具体模型）· 能力驱动（调度只读能力画像）· 动态工作流（流程按上下文组装）。

## 与 ugc-creative-amplifier 的分层
- `ugc-creative-amplifier`（保留不动）：创意业务层（钩子角度/混剪/矩阵/钩子库/成本×质量）。
- `universal-video-generator`（本项目）：底层（统一接口/选型/声纹锁/动态编排/长视频/自进化/商用服务）。

## 诚实边界
真实生成需各家 API Key + ffmpeg；多副本扩展把 `FileJobStore` 换 Redis/DB + 外部队列 + S3 存储；隐式质量可接 Supermetrics 真实 CTR/GMV 校准；P1/P2 适配器待补真实调用（搜 `ADAPTER_NOT_IMPLEMENTED`/`// VERIFY`）；Sora 已 2026-04 关停仅留占位。
