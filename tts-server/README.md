# tts-server — 本地开源 TTS 适配服务（方案 A · 完全离线 · 零云成本）

把 MOSS-TTS-Nano / Kokoro / Piper 封装成视频工厂 `LocalTtsAdapter` 期望的统一 HTTP 契约。CPU 即可，不联网。

## 契约
- `POST /tts` `{text, voice?, language?, emotion?, speed?, format?}` → `{audio_url, path, duration, ms}`
- `POST /clone` `{name, sample, language?}` → `{voice_id}`（仅 MOSS 支持克隆）
- `GET /health` → `{ok, backend}`；`GET /audio/<file>` → 音频文件

## 启动（三选一后端）
```bash
pip install -r requirements.txt

# 1) MOSS-TTS-Nano（推荐：中文强 + 情绪 + 克隆 + Apache-2.0 可商用）
git clone https://github.com/OpenMOSS/MOSS-TTS-Nano && pip install -e MOSS-TTS-Nano
TTS_BACKEND=moss TTS_PORT=9881 python app.py

# 2) Kokoro（更快，无情绪）
pip install kokoro soundfile numpy
TTS_BACKEND=kokoro TTS_PORT=9881 python app.py

# 3) Piper（超轻，中文一般）
#   下载 piper 可执行 + zh_CN-huayan-medium.onnx
TTS_BACKEND=piper PIPER_MODEL=zh_CN-huayan-medium.onnx TTS_PORT=9881 python app.py
```

## 接入视频工厂
在主项目 `.env` 配：
```
LOCAL_TTS_ENABLED=true
LOCAL_TTS_BASE_URL=http://localhost:9881
LOCAL_TTS_MODEL=moss
```
之后引擎"省钱优先"会自动选本地 TTS（成本 0），**不再调用任何云 TTS API**。

## 环境变量
`TTS_BACKEND`(moss/kokoro/piper) · `TTS_PORT`(9881) · `TTS_HOST`(0.0.0.0) · `TTS_OUT_DIR`(./tts_out) · `TTS_PUBLIC_HOST`(audio_url 用的主机名) · `MOSS_MODEL` / `KOKORO_LANG` / `PIPER_MODEL` / `PIPER_BIN`。

## 说明
各后端真实调用处标了 `VERIFY/TODO`——按你装的版本核对一次方法名即可（不同版本 API 略有差异）。沙箱无法实跑，部署到你服务器后 `curl -XPOST localhost:9881/tts -d '{"text":"你好"}' -H 'content-type: application/json'` 自测。
