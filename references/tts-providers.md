# TTS / 声纹克隆清单与能力画像

> 统一 `TTSProvider` 接口。**声纹一致性的核心是 `VoiceLockManager` + `@voice1`**：全片锁一个声纹，
> 无论底下换哪个 TTS，对上层都是同一 `lockId` → 音色天然一致；克隆一次，全矩阵复用。

| 工具 | 接入 | 克隆 | 样本时长 | 语言 | 美元/千字 | 优先级 | 适配器 |
|---|---|---|---|---|---|---|---|
| CosyVoice 3.0（阿里开源） | 本地 | 零样本 | 3–30s | 中/方言/英/日/粤 | **0（自部署）** | P0·样板 | `CosyVoiceAdapter`（真实实现） |
| ElevenLabs | 云API | 即时(零样本) | 10–300s | 多语言 | ~0.30 | P0·样板 | `ElevenLabsAdapter`（真实实现） |
| 火山引擎 声音复刻 | 云API | 少样本 | 5–120s | 中/英 | ~0.02 | P0 | `stubs.VolcengineVoiceAdapter` |
| GPT-SoVITS（开源） | 本地 | 少样本 | 5–120s | 中/英/日 | **0** | P0 | `stubs.GptSoVITSAdapter` |
| Gemini TTS | 云API | 否 | — | 多语言 | ~0.015 | P1 | `stubs.GeminiTTSAdapter` |
| OpenAI TTS | 云API | 否 | — | 多语言 | ~0.015 | P1 | `stubs.OpenAITTSAdapter` |

## @voice1 决策（VoiceLockManager.lockVoice）
```
有真人口播样本 → kind:"clone" → 选支持克隆且最省的 provider（默认偏本地 CosyVoice/GPT-SoVITS，0 成本）
无真人样本     → kind:"builtin" → 选一个内置音色
之后全片/全矩阵 synthesizeWithVoice("voice1", text) → 统一音色
```
- **降本**：量大时优先用 CosyVoice/GPT-SoVITS（自部署 0 边际成本）。
- **出海/多语言**：优先 ElevenLabs。
- **抖音生态稳定云服务**：火山引擎声音复刻。
