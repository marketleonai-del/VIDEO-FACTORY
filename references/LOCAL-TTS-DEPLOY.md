# 本地 TTS 部署指南（方案 A · 完全离线 · 零云成本）

> 目标：你拿到代码，在自己服务器上把"带语音的完整视频工厂"跑起来——**不调任何云 TTS API**。
> 选型见 `references/TTS-SELECTION.md`（主选 MOSS-TTS-Nano）。沙箱无法实跑，以下命令在你的 Linux/Mac 服务器执行。

## 架构（数据流）
```
浏览器(前端语音设置:情绪/音色/语速/开关)
   └─ POST /api/generate ─▶ web/server.ts
                              ├─ 逐段生成视频(可灵/Seedance… 你的 Key) → ffmpeg 拼接(LongFormAssembler)
                              ├─ 文本 ─▶ LocalTtsAdapter ─HTTP─▶ tts-server(本地MOSS/Kokoro/Piper) → 语音wav  ★无云
                              └─ AudioVideoMuxer(ffmpeg) 把语音配到视频 → 最终带语音成片
```
要点：视频模型仍需你的 Key（视频本就要花钱）；**语音完全本地、零成本**，这正是省下的部分。

## 一、装 + 起 本地 TTS 服务
```bash
cd universal-video-generator/tts-server
pip install -r requirements.txt

# 主选 MOSS-TTS-Nano（中文强 + 情绪 + 克隆 + Apache-2.0 可商用）
git clone https://github.com/OpenMOSS/MOSS-TTS-Nano && pip install -e MOSS-TTS-Nano
TTS_BACKEND=moss TTS_PORT=9881 python app.py
# 自测：curl -XPOST localhost:9881/tts -H 'content-type: application/json' -d '{"text":"你好，世界","emotion":"happy"}'
```
备选：`TTS_BACKEND=kokoro`（`pip install kokoro soundfile numpy`，更快无情绪）/ `TTS_BACKEND=piper`（超轻，中文一般）。

## 二、装 ffmpeg（拼接 + 配音必需）
```bash
# Ubuntu/Debian: sudo apt update && sudo apt install -y ffmpeg
# Mac: brew install ffmpeg
ffmpeg -version   # 确认可用
```

## 三、配置视频工厂 + 起服务
```bash
cd universal-video-generator
cp .env.example .env
# 编辑 .env：
#   视频模型 Key（任选一家有就行）：KLING_API_KEY=... 或 SEEDANCE_API_KEY=... 等
#   本地 TTS（关键）：
#     LOCAL_TTS_ENABLED=true
#     LOCAL_TTS_BASE_URL=http://localhost:9881
#     LOCAL_TTS_MODEL=moss
#     FFMPEG_BIN=ffmpeg
npm install
npm run build
node dist/web/server.js          # 默认 http://localhost:8080
```
打开 `http://你的服务器:8080`：输入文字 + 选时长 + 选情绪/音色/语速 → 生成。后端会逐段生成视频→拼接→本地合成语音→配到视频上→返回带语音成片。

## 四、两种模式
- **方案 A（本指南 / 用户本地）**：用户在自己机器起 `tts-server` + web 服务，完全离线、零云费、隐私最佳。
- **方案 B（你部署的 SaaS）**：你在服务器起一套（同样自托管 TTS），所有用户共享，仍是零云 TTS 费；可叠加主项目的多租户鉴权/配额（见 `COMMERCIAL-READINESS.md`）。

## 五、音画同步 / 时长对齐（AudioVideoMuxer 已实现）
- `fit:"pad"`：语音短→补静音到视频长（默认，最稳）。
- `fit:"trim"` + `-shortest`：按较短流裁，保 A/V 同步。
- `fit:"atempo"`：轻微变速让语音贴合视频长（0.9–1.1 自然）。
- 采样率统一 `aresample=44100`；视频 `-c:v copy` 不重编码→**无画质损失**；可选 BGM `amix` 压低混入。

## 六、性能 / 排错
- MOSS-Nano CPU 上 10s 语音通常数百毫秒~数秒；模型 ~数百 MB；首次加载稍慢（懒加载）。
- TTS 服务连不上 → 前端/后端会跳过配音只出无声视频（优雅降级）；检查 `LOCAL_TTS_BASE_URL` 与 `curl /health`。
- 各后端真实 API 略有差异 → `tts-server/app.py` 中搜 `VERIFY/TODO`，按你装的版本核对一次方法名。
- ffmpeg 未装 → 拼接/配音输出"命令计划"而非文件；装上即真渲染。

## 七、自进化结合（可选）
TTS 使用参数（情绪/音色/语速 类别，不含文本内容）可作为隐式信号喂自进化（`Telemetry` opt-in、匿名）：哪些情绪/音色最受欢迎 → HQ 聚合后优化默认值。开关在前端页脚（默认关）。
