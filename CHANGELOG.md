# Changelog

## 3.2.0 — macOS 苹果版（桌面 App + 一键启动）
- 新增 `desktop/`：Electron 外壳（`main.js`+`package.json`），把 web 引擎包成原生 Mac App——`npm run dist:mac` 出 `.dmg`（Apple Silicon **arm64** + Intel **x64**），打包用 Electron 自带 Node，终端用户无需另装 Node；同配置也能出 Windows `.exe`。
- 新增 `mac/start.command`：双击即用启动器（检查 Node/ffmpeg → 必要时装依赖+编译 → 起服务 → 自动开浏览器到 :8080）。
- 新增 `MAC-SETUP.md`：苹果版总指南（Homebrew/Apple Silicon 原生 arm64、三种用法、Gatekeeper 绕过、本地 TTS on Mac 的 MPS 加速、常见问题、Win 对照）。
- 引擎本就跨平台（无平台分支：`FFMPEG_BIN` 回退 `ffmpeg`、TTS 走 Python、均 spawn），故无 TS 改动。

## 3.1.0 — 本地开源 TTS（无云 API·降本）
- 蜂群并行调研开源 TTS，选定 **MOSS-TTS-Nano**（0.1B·CPU·中文强·情绪·Apache-2.0 可商用）主选，Kokoro/Piper 兜底；报告 `references/TTS-SELECTION.md`。
- 新增 `adapters/tts/LocalTtsAdapter.ts`：本地自托管 TTS（MOSS/Kokoro/Piper），`local-self-hosted`、**成本 0**、情绪/音色/语速/克隆；`createEngine` 已注册默认 moss，引擎"省钱优先"**优先选它而非云 TTS**。
- 新增 `core/longvideo/AudioVideoMuxer.ts`：ffmpeg 配音（对齐 apad/atempo/-shortest、采样率归一、BGM 混音、`-c:v copy` 无损）。
- `.env` 加 `LOCAL_TTS_ENABLED/BASE_URL/MODEL`；`Config.ts` 映射。
- 待办（VM 恢复后）：tsc 校验 + 跑测试 + web 语音设置 UI + 重打包 .skill。

## 3.0.0 — 长视频 + 自进化
- **长视频**：`LongVideoPlanner`（按模型上限分段 + 续帧一致 + 主体恒定 + 每 N 段再锚定防漂移 + 混合用户素材）+ `LongFormAssembler`（归一化分辨率/帧率 + concat/xfade）+ `LongVideoPipeline`（端到端编排）。
- **自进化**：`SkillParams`（可训练参数）+ `Telemetry`（匿名 opt-in 无 PII 隐式遥测）+ `QualitySignals`（无需打分的隐式质量）+ `EvolutionEngine`（bandit + SkillOpt 验证门控严格胜出 + 文本学习率 + 负样本缓冲 + 回滚）+ `Reporter`/`VersionChecker`（匿名上报/拉取门控采纳）。
- **总部 HQ MVP**：`hq/collector`（/telemetry、/params/latest、/admin/promote、/admin/dashboard）+ `hq/aggregate`（按版本聚合隐式质量 + 晋升/回滚决策）。
- **CLI**：新增 `long`（长视频）、`evolve`（跑一轮自进化）。示例：`example:long`、`example:evolve`。
- **设计文档**：`EVOLUTION-SYSTEM-DESIGN.md`（8 交付物 + 风险 + 分阶段 + 可接入的 skill/agent/连接器）。
- 验证：tsc 0 错 + **46 单测通过**（core/runtime/commercial/server/evolution/longvideo）+ 长视频/自进化 demo 跑通。

## 2.0.0 — 商用水准
- 多租户鉴权 + 配额/计量 + 幂等 + 持久任务 + 韧性(熔断/超时) + 可观测(JSON日志/指标/reqId) + 商用 server(/v1/*、/health、/ready、/metrics) + Webhook + 优雅关停 + 存储抽象 + Docker/CI + API.md + 商用就绪报告。

## 1.1.0 — 生产化
- 全部适配器独立文件（P0 可跑 + P1/P2 骨架）；运行时重试/并发/限流/日志；批量并发 BatchExecutor；配置/校验；FFmpegAssembler；CLI + HTTP server。

## 1.0.0 — 首版
- 统一 VideoModel/TTSProvider 接口 + 能力画像 + 选型 + @voice1 声纹锁 + 动态工作流 + 成本估算 + 120 门质检 + 样板适配器。
