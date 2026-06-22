---
name: universal-video-generator
description: 通用视频生成接口层 + 动态工作流编排器 + 长视频拼接 + 自进化。类比 LangChain 统一所有 LLM——本 skill 统一所有 AI 视频模型（Seedance/可灵/Veo/Runway/Wan…）与 TTS/声纹克隆工具（CosyVoice/GPT-SoVITS/火山/ElevenLabs…）。核心：①统一接口+能力画像；②能力驱动选型+回退；③@voice1 声纹锁；④动态工作流（按预算/素材/平台/矩阵组装）；⑤成本估算+120门质检；⑥多租户/配额/计量/幂等/持久/熔断/可观测/Webhook（商用）；⑦长视频（多段续帧一致+拼接+混合用户素材）；⑧自进化（匿名隐式遥测→验证门控→学习率→回滚，SkillOpt/Hermes 思路）；⑨插件式（新增模型只加 Adapter）。是 ugc-creative-amplifier 的底层通用化改造。自动触发：换/接入/统一视频模型或 TTS、按预算选模型、动态编排、长视频拼接、自进化/数据上报、给新模型写适配器、部署成服务时命中。显式指令：/接入模型 /选型 /锁声纹 /编排 /长视频 /自进化 /成本 /适配器 /部署。
---

# universal-video-generator（通用视频生成引擎）v3.0

> **一句话**：统一所有视频/TTS 工具的接口 + 动态工作流 + 长视频拼接 + 自进化，并具备商用 SaaS 形态。换模型像换插件，越用越好。
> **与 ugc-creative-amplifier 的关系**：那套（钩子角度/混剪/矩阵）是业务层，原样保留；本 skill 是其下的接口/适配/工作流/长视频/自进化/服务层。
> **三原则**：插件式 · 能力驱动 · 动态工作流。

---

## ⚠️ 开场强制自检（收到先做）
```
【自检报告】
① 形态：通用视频接口层 + 动态工作流 + 长视频 + 自进化 + 商用服务
② 三层架构：业务层 → 抽象层(VideoModel/TTSProvider+能力画像+调度) → 适配层(各工具Adapter)
③ @voice1 声纹锁：全片单一声纹，AI画面静音，统一音轨 → 音色一致
④ 动态工作流：WorkflowBuilder 按 模式/预算/素材/矩阵/平台 组装 14 Stage
⑤ 长视频：LongVideoPlanner 分段(≤模型上限)+续帧+主体恒定+每N段再锚定；LongFormAssembler 归一化+concat/xfade；支持混合用户素材
⑥ 自进化：SkillParams(可训练参数)+Telemetry(匿名opt-in无PII)+QualitySignals(隐式质量,不需打分)+EvolutionEngine(bandit+验证门控严格胜出+学习率+负样本+回滚)
⑦ 商用：多租户鉴权+配额/计量+幂等+持久+熔断/超时+可观测+Webhook+优雅关停
⑧ 韧性/质检/成本：重试/限流/回退；QualityGate 120门；CostEstimator 任意组合估价
⑨ 插件式：新增模型只写 Adapter，核心零改动
⑩ 不做：不替代业务层创意逻辑(在 ugc-creative-amplifier)；不在沙盒真连各家 API(需 Key)
【自检完毕。告诉我：接入/切换模型、选型、锁声纹、编排、长视频、自进化、还是部署成服务？】
```

---

## 文件结构
```
core/            接口/注册表/声纹锁/成本/质检 + runtime/resilience/observability/errors/Config
                 + JobStore/FileJobStore/Storage/Queue + Auth/Quota/Metering + FFmpegAssembler/BatchExecutor
core/longvideo/  LongVideoPlanner + LongFormAssembler + LongVideoPipeline（长视频）
core/evolution/  SkillParams + Telemetry + QualitySignals + EvolutionEngine + Reporter + VersionChecker（自进化）
workflows/       Stage/Workflow/Builder + 14 标准 Stage
adapters/        video: Kling/Runway/Seedance/Veo(可跑)+skeletons；tts: ElevenLabs/CosyVoice/Volcengine/GptSoVITS(可跑)+skeletons
presets/         platforms + budgets
hq/              collector + aggregate（总部端 HQ MVP：收集/聚合/晋升/回滚）
bin/cli.ts       CLI(generate/long/evolve/health)   server.ts  商用 HTTP   .env.example
references/ examples/ test/  + API.md / COMMERCIAL-READINESS.md / EVOLUTION-SYSTEM-DESIGN.md / Dockerfile / CI
index.ts         createEngine()/createEngineFromEnv()
```

## 长视频（v3.0）
超过单模型上限自动**多段生成 + 拼接**；一致性 = **续帧**（image2video 自上段末帧）+ **主体恒定**（subjectAnchor）+ **每 N 段用产品图再锚定**（防累积漂移）；段间 `hard`/`xfade`；拼接前**归一化分辨率/帧率/SAR**；支持**混合模式**（用户素材片段 + AI 补缺口）。
```bash
node dist/bin/cli.js long --duration 60 --aspect 9:16 --subject "同一主播+同款产品" --transition xfade --out long.mp4
```

## 自进化（v3.0）——越用越好，且不偷数据
- **可训练参数** `SkillParams`（角度权重/模型偏置/提示后缀/质检门）——SkillOpt 思路。
- **隐式质量**：不需用户打分；用 成功率/重生成率/质检通过/拼接成功/重试 合成（`QualitySignals`）。
- **进化** `EvolutionEngine`：bandit 偏好高质量角度 → 候选 → **验证门控（候选须严格优于当前，no ties）** → 学习率限幅采纳 → 负样本缓冲 → 可回滚。
- **隐私第一**：`Telemetry` 默认**关**（opt-in：`UVG_TELEMETRY=on`），匿名 id（哈希）、白名单只采隐式信号、绝不采脚本/素材/产品名、开源可审计。
- **总部 HQ**（`hq/`）：跨用户匿名聚合 → 按版本比质量 → 晋升/回滚经验证的更优参数 → `/params/latest` 下发，客户端本地再门控才采纳。
```bash
node dist/bin/cli.js evolve            # 跑一轮（demo 合成遥测）
node dist/hq/collector.js              # 启动总部端 HQ
```

## 商用（v2.0 保留）
多租户鉴权 + 配额/计量 + 幂等 + 持久任务 + 熔断/超时 + 结构化日志/指标 + Webhook + 优雅关停；HTTP `/v1/generate`、`/v1/jobs/:id`、`/v1/usage`、`/health` `/ready` `/metrics`；`docker compose up`。详见 `API.md` / `COMMERCIAL-READINESS.md`。

## 用法速览
```bash
cp .env.example .env && npm i && npm run build
node dist/bin/cli.js generate --product "便携榨汁杯" --count 20   # 矩阵
node dist/bin/cli.js long --duration 60 --transition xfade        # 长视频
node dist/bin/cli.js evolve                                       # 自进化
node dist/server.js                                               # 商用服务
# 离线 demo（无需 Key）：npm run example / example:batch / example:long / example:evolve
```

## 可接入的 skill/agent/连接器
真实效果信号→**Supermetrics**（CTR/GMV 校准自进化质量）；行为遥测后端→**Amplitude**；进化周报→`marketing:performance-report`；创意业务层→`ugc-creative-amplifier`；设计分发→Canva/Cloudinary；暴露为 MCP→`mcp-builder`。详见 `EVOLUTION-SYSTEM-DESIGN.md` 第 9 节。

## 立即执行
```
universal-video-generator v3.0 已就绪（通用接口 + 动态工作流 + 长视频 + 自进化 + 商用服务）。
告诉我：① 接入/切换模型 ② 短视频矩阵 / 长视频拼接 ③ 有无真人口播(定 @voice1) ④ 开自进化/连 Supermetrics ⑤ 部署成服务 ⑥ 写新适配器？
```
