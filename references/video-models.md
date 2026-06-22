# 视频模型清单与能力画像

> 所有模型通过统一 `VideoModel` 接口接入。选型/成本/回退全部读「能力画像」，不硬编码模型名。
> 价格为 2026 年公开按秒价估算，接入前以各平台官网为准。

| 模型 | 接入 | 模式 | 时长 | 原生音频 | 美元/秒 | 优先级 | 适配器 |
|---|---|---|---|---|---|---|---|
| 可灵 Kling | 云API | t2v/i2v/v2v | 5–120s | 否 | ~0.07 | P0·样板 | `KlingAdapter`（真实实现） |
| Runway Gen-4 | 云API | t2v/i2v | 2–16s | 否 | ~0.05 | P0·样板 | `RunwayAdapter`（真实实现） |
| Seedance 2.0（即梦） | 云API | t2v/i2v | 4–12s | **是★** | ~0.20 | P0 | `stubs.SeedanceAdapter`（画像全，调用待补） |
| Veo 3.1 | 云API | t2v/i2v | 4–8s | 是 | 0.05–0.40 | P0 | `stubs.VeoAdapter` |
| Wan 2.6 | 本地/开源 | t2v/i2v | 2–10s | 否 | ~0.005 | P1 | `stubs.WanAdapter` |
| Gemini Omni | 云API | t2v | — | — | ~0.12 | P1 | `stubs.GeminiOmniAdapter` |
| Codex（视频） | 云API | t2v | — | — | ~0.10 | P1 | `stubs.CodexAdapter`（待调研 API） |
| 悟空 Wukong（字节） | 云API | t2v/i2v | — | — | ~0.10 | P1 | `stubs.WukongAdapter`（待调研 API） |
| Wokebuddy / Qclaw / Openclaw | — | t2v | — | — | ~0.10 | P2 | `stubs.*`（占位，待调研 API） |
| Sora | 云API | t2v | — | — | — | P2 | `stubs.SoraAdapter`（**已 2026-04 关停，仅留接口**） |

## 关键提醒
- **带原生音频的模型（Seedance/Veo）在混剪时必须静音**：音色统一交给 `@voice1`（见 tts-providers / VoiceLockManager）。`GenerateParams.wantNativeAudio=false`。
- **缺 Key 时样板适配器进入 demo 模式**：返回占位 `demo://` 结果，便于离线跑通工作流；接真 Key 即真实生成。
- **新增模型**：见 `adapter-guide.md`，只写一个 Adapter，核心零改动。
