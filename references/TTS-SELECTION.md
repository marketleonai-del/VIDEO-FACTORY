# 本地开源 TTS 选型报告（无云 API · 降本）

> 目标：用**本地开源 TTS** 替代云 TTS API → **零 TTS 调用成本**；要求轻量、CPU 可跑、中文好、能调情绪、**可商用**。
> 结论先行：**主选 MOSS-TTS-Nano**（0.1B · CPU · 中文强 · Apache-2.0 可商用 · 情绪/克隆），**备选 Kokoro**（82M · Apache-2.0 · 最快），**兜底 Piper**（MIT · 极轻）。由蜂群双 Agent 并行调研得出。

## 对比表
| 项目 | 参数 | CPU可跑 | 中文 | 情绪 | 音色/克隆 | 许可证·可商用 | 部署 |
|---|---|---|---|---|---|---|---|
| **MOSS-TTS-Nano** ★主选 | ~0.1B | ✅ 4核流式/M4单核 | 强(WER 0.83%, 48kHz) | ✅ 提示词驱动(无固定列表) | 预置音色 + 零样本克隆(5-10s) | **Apache-2.0 ✅明确可商用** | `pip -e .` + 自带 FastAPI HTTP 服务 |
| **Kokoro** 备选 | 82M | ✅ 快于实时 | 良(`Kokoro-82M-v1.1-zh`, 100音色) | ❌ 无原生情绪 | 54音色 / ❌无克隆 | **Apache-2.0 ✅** | pip/ONNX, 有FastAPI封装 |
| **Piper** 兜底 | ~10-30M | ✅ 树莓派实时 | 有zh但质量最弱 | ❌ | 100+音色 / ❌ | **MIT ✅** | 极易(ONNX单文件) |
| ChatTTS | ~300M | 跑得动,推荐GPU | 好 | ✅标签(laugh/break) | 采样说话人+克隆 | **CC-BY-NC ❌ 不可商用** | pip(还故意加噪) |
| Fish Speech / OpenAudio | 0.5B+ | 偏GPU,CPU重 | 极好 | ✅50+情绪标记 | 克隆(~10s) | **自定义(非免费商用)❌需付费授权** | 中-难 |
| Supertonic v3 | ~99M | ✅极快(~167x实时) | 不确定(zh未明确) | 预置+表情标签 | 100s音色+克隆 | OpenRAIL-M(有限制需法务) | ONNX/端侧 |

## 为什么选 MOSS-TTS-Nano
唯一**同时满足全部硬指标**：①0.1B 真·CPU 可跑（甚至 M4 单核）②中文确认强（0.83% WER, 48kHz）③Apache-2.0 **商用无歧义** ④情绪 + 零样本克隆 ⑤自带 FastAPI HTTP 服务，集成简单。正好对应你在抖音看到的"0.1B、不吃显卡、能调情绪"。
- **缺点**：情绪是"提示词/参考音驱动"而非固定情绪枚举——上线前用官方 demo 验一下情绪可控度是否达标。
- **兜底策略**：要更快/不需要情绪→Kokoro；超低配/中文要求不高→Piper。三者都 Apache/MIT 可商用，**全程无云 TTS 费用**。

## 怎么接（无云 API）
本仓库新增 `adapters/tts/LocalTtsAdapter.ts`：统一对接**本地自托管 TTS HTTP 服务**（MOSS/Kokoro/Piper 都能跑成一个本地 `/tts` 服务），`deploymentType=local-self-hosted`、`costPerThousandChars=0`。引擎选型已"省钱+本地优先"，会**默认走它而非云 TTS**。
合成出的语音由新增 `core/longvideo/AudioVideoMuxer.ts` 用 ffmpeg **配到视频上**（对齐时长/采样率/可选 BGM/音量）。

### 本地部署（MOSS-TTS-Nano，示例）
```bash
git clone https://github.com/OpenMOSS/MOSS-TTS-Nano && cd MOSS-TTS-Nano
pip install -e .            # 需 pynini / WeTextProcessing
python -m moss_tts.server  # 启动自带 FastAPI（默认本地端口）
# 在 .env 配 LOCAL_TTS_ENABLED=true、LOCAL_TTS_BASE_URL=http://localhost:<port>、LOCAL_TTS_MODEL=moss
```
- 10s 语音生成耗时（CPU）：通常数百毫秒~数秒级；模型文件 ~数百 MB；纯离线、不联网、不上传文本。

## 两种模式
- **本地/纯前端**：用户本机起 TTS 服务，完全离线、零成本、隐私最佳。
- **后端 SaaS**：你在服务器起一个 TTS 服务，所有租户共享，仍是**自托管零云费**。

来源（蜂群调研）：MOSS-TTS-Nano [GitHub](https://github.com/OpenMOSS/MOSS-TTS-Nano) · [HF](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Nano-100M)；[Kokoro](https://github.com/hexgrad/kokoro) · [Kokoro-zh](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh)；[Piper](https://github.com/rhasspy/piper)；[ChatTTS LICENSE](https://github.com/2noise/ChatTTS/blob/main/LICENSE)；[Fish Speech LICENSE](https://github.com/fishaudio/fish-speech/blob/main/LICENSE)；[Supertonic](https://github.com/supertone-inc/supertonic)。
