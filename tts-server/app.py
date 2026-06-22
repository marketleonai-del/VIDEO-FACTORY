"""
tts-server/app.py — 本地开源 TTS 适配服务（方案 A：完全离线，零云 API 成本）

把 MOSS-TTS-Nano / Kokoro / Piper 统一封装成本工厂 LocalTtsAdapter 期望的 HTTP 契约：
  POST /tts    {text, voice?, language?, emotion?, speed?, format?} -> {audio_url, path, duration}
  POST /clone  {name, sample, language?}                            -> {voice_id}
  GET  /health                                                      -> {ok, backend}
  GET  /audio/<file>                                                -> 返回音频文件（供 audio_url 播放/下载）

后端由环境变量 TTS_BACKEND 选择：moss(默认) | kokoro | piper
零依赖云服务；CPU 即可。各后端的真实调用处标注 VERIFY/TODO —— 你在自己服务器按所装版本核对一次即可。
"""
import os
import time
import uuid
import wave
import contextlib
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

BACKEND = os.environ.get("TTS_BACKEND", "moss").lower()
OUT_DIR = os.environ.get("TTS_OUT_DIR", "./tts_out")
HOST = os.environ.get("TTS_HOST", "0.0.0.0")
PORT = int(os.environ.get("TTS_PORT", "9881"))
os.makedirs(OUT_DIR, exist_ok=True)

app = FastAPI(title="universal-video-generator local TTS", version="1.0")


class TtsReq(BaseModel):
    text: str
    voice: str | None = "default"
    language: str | None = "zh"
    emotion: str | None = None
    speed: float | None = 1.0
    format: str | None = "wav"


class CloneReq(BaseModel):
    name: str
    sample: str  # 本地音频路径或可访问 URL
    language: str | None = "zh"


# ----------------------------------------------------------------------------
# 后端实现（懒加载，按 TTS_BACKEND 选一个）。返回写好的 wav 文件路径。
# ----------------------------------------------------------------------------
_engine = None


def _load_engine():
    """懒加载所选后端模型（首次调用时）。"""
    global _engine
    if _engine is not None:
        return _engine
    if BACKEND == "moss":
        # MOSS-TTS-Nano (0.1B, CPU, 情绪, 克隆, Apache-2.0)
        # VERIFY: 按 github.com/OpenMOSS/MOSS-TTS-Nano 的实际 Python API 核对类名/方法
        from moss_tts import MossTTS  # type: ignore  # pip install -e MOSS-TTS-Nano
        _engine = MossTTS.from_pretrained(os.environ.get("MOSS_MODEL", "OpenMOSS-Team/MOSS-TTS-Nano-100M"))
    elif BACKEND == "kokoro":
        # Kokoro (82M, 快, Apache-2.0, 无情绪)
        from kokoro import KPipeline  # type: ignore  # pip install kokoro
        _engine = KPipeline(lang_code=os.environ.get("KOKORO_LANG", "z"))  # 'z' = 中文
    elif BACKEND == "piper":
        _engine = "piper-cli"  # 通过子进程调用 piper 可执行文件
    else:
        raise RuntimeError(f"未知 TTS_BACKEND: {BACKEND}")
    return _engine


def _synth(req: TtsReq) -> tuple[str, float]:
    """合成 -> (wav 文件绝对路径, 时长秒)。"""
    out = os.path.join(OUT_DIR, f"{uuid.uuid4().hex}.wav")
    eng = _load_engine()

    if BACKEND == "moss":
        # VERIFY: MOSS 的合成方法签名（情绪/语速/音色/克隆音色 id）
        eng.tts(
            text=req.text,
            speaker=req.voice or "default",
            emotion=req.emotion,      # MOSS 情绪为提示词/参考驱动
            speed=req.speed or 1.0,
            language=req.language or "zh",
            output_path=out,
        )
    elif BACKEND == "kokoro":
        # Kokoro: KPipeline 产出 (graphemes, phonemes, audio) 流；拼接后写 wav
        import soundfile as sf  # type: ignore
        import numpy as np  # type: ignore
        chunks = []
        for _, _, audio in eng(req.text, voice=req.voice or "zf_001", speed=req.speed or 1.0):
            chunks.append(audio)
        data = np.concatenate(chunks) if chunks else np.zeros(1)
        sf.write(out, data, 24000)
    elif BACKEND == "piper":
        import subprocess
        model = os.environ.get("PIPER_MODEL", "zh_CN-huayan-medium.onnx")
        # piper 读 stdin 文本，输出 wav 文件
        subprocess.run(
            [os.environ.get("PIPER_BIN", "piper"), "-m", model, "-f", out, "--length_scale", str(1.0 / (req.speed or 1.0))],
            input=req.text.encode("utf-8"), check=True,
        )

    dur = 0.0
    try:
        with contextlib.closing(wave.open(out, "rb")) as w:
            dur = w.getnframes() / float(w.getframerate())
    except Exception:
        pass
    return out, dur


def _clone(req: CloneReq) -> str:
    """零样本/少样本克隆 -> 返回可用于 /tts 的 voice id。"""
    if BACKEND == "moss":
        # VERIFY: MOSS 零样本克隆注册接口（5-10s 样本）
        eng = _load_engine()
        return eng.register_speaker(name=req.name, ref_audio=req.sample)  # type: ignore
    # Kokoro/Piper 无克隆 -> 退回到内置音色
    return req.name


# ----------------------------------------------------------------------------
# HTTP 路由
# ----------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "backend": BACKEND}


@app.post("/tts")
def tts(req: TtsReq):
    t0 = time.time()
    path, dur = _synth(req)
    fname = os.path.basename(path)
    return {
        "audio_url": f"http://{_public_host()}:{PORT}/audio/{fname}",
        "path": os.path.abspath(path),
        "duration": dur,
        "ms": int((time.time() - t0) * 1000),
        "backend": BACKEND,
    }


@app.post("/clone")
def clone(req: CloneReq):
    return {"voice_id": _clone(req)}


@app.get("/audio/{fname}")
def audio(fname: str):
    p = os.path.join(OUT_DIR, os.path.basename(fname))
    if not os.path.exists(p):
        return {"error": "not found"}
    return FileResponse(p, media_type="audio/wav")


def _public_host() -> str:
    return os.environ.get("TTS_PUBLIC_HOST", "localhost")


if __name__ == "__main__":
    import uvicorn
    print(f"[tts-server] backend={BACKEND} on http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)
