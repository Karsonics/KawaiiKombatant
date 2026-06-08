#!/usr/bin/env python3
"""
F5-TTS Standalone API Server
=============================
Wraps the F5-TTS library in a lightweight FastAPI server on port 5050.

Install first:
    pip install f5-tts torch torchaudio --index-url https://download.pytorch.org/whl/rocm7.2

Start:
    PYTORCH_ROCM_ARCH=gfx1200 python f5_tts_server.py --port 5050

API:
    POST /v1/tts     — {"text":"...","ref_audio_path":"...","ref_text":"...","nfe_steps":32,"speed":1.0}
    GET  /health      — {"status":"ok"}
"""

import argparse
import io
import os
import sys
import tempfile
import time
import wave

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="F5-TTS Server")
_model = None
_start_time = time.time()


class TTSRequest(BaseModel):
    text: str
    ref_audio_path: str = ""
    ref_text: str = ""
    nfe_steps: int = 32
    speed: float = 1.0


class TTSResponse(BaseModel):
    status: str
    generation_time: float
    audio_duration: float
    sample_rate: int


@app.on_event("startup")
async def startup():
    global _model
    print("Loading F5-TTS v1 Base model...")
    try:
        from f5_tts import F5TTS
    except ImportError:
        print("[FATAL] f5-tts not installed. Run: pip install f5-tts")
        sys.exit(1)

    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if torch.cuda.is_available():
        print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"Device: {device}")

    _model = F5TTS(
        model="F5TTS_v1_Base",
        device=device,
    )
    print("Model loaded. Ready.")


@app.get("/health")
async def health():
    import torch
    return {
        "status": "ok",
        "uptime": int(time.time() - _start_time),
        "model": "F5TTS_v1_Base",
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
    }


@app.post("/v1/tts")
async def tts(req: TTSRequest):
    global _model
    if _model is None:
        raise HTTPException(503, "Model not loaded yet")

    if not req.ref_audio_path or not os.path.exists(req.ref_audio_path):
        raise HTTPException(400, f"ref_audio_path not found: {req.ref_audio_path}")

    t0 = time.perf_counter()
    try:
        audio, sr = _model.generate(
            text=req.text,
            ref_audio=req.ref_audio_path,
            ref_text=req.ref_text or None,
            nfe_step=req.nfe_steps,
            speed=req.speed,
        )
    except Exception as e:
        raise HTTPException(500, f"Generation failed: {e}")

    elapsed = time.perf_counter() - t0

    # Write WAV
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        audio_16 = (audio * 32767).astype(np.int16)
        wf.writeframes(audio_16.tobytes())

    duration = len(audio) / sr
    print(f"TTS: {elapsed:.2f}s | audio: {duration:.2f}s | RTF: {elapsed/duration:.2f}x")
    return buf.getvalue()


def main():
    parser = argparse.ArgumentParser(description="F5-TTS API Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=5050, type=int)
    parser.add_argument("--model", default="F5TTS_v1_Base")
    args = parser.parse_args()

    print(f"Starting F5-TTS on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
