import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from server.kuro_engine import KuroEngine
from server.process.asr_func.asr_push_to_talk import transcribe_bytes as asr_transcribe
from utils.logging import logger

engine = KuroEngine()
_mood_subscribers: set[WebSocket] = set()
app = FastAPI(title="Kuro V-Tuber Engine")


_web_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "web"
if _web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_web_dir)), name="web_static")
    app.mount("/models", StaticFiles(directory=str(_web_dir / "models")), name="web_models")

    @app.get("/")
    async def web_root():
        return FileResponse(str(_web_dir / "index.html"))

    @app.get("/settings")
    async def web_settings():
        return FileResponse(str(_web_dir / "settings.html"))

    logger.info("Web UI static files mounted from %s", _web_dir)


@app.on_event("shutdown")
async def shutdown():
    engine.close()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "sessions": engine.get_session_count(),
        "tts_enabled": engine.tts_enabled,
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    session_id: str | None = None
    logger.info("WebSocket client connected")

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)

            msg_type = msg.get("type")

            if msg_type == "message":
                user_input = msg.get("data", "").strip()
                if not user_input:
                    continue

                if not session_id:
                    session_id = msg.get("session_id") or engine.create_session()

                loop = asyncio.get_running_loop()
                try:
                    result = await loop.run_in_executor(
                        None, engine.process_message, user_input, session_id
                    )
                    session_id = result["session_id"]
                    await ws.send_json({"type": "done", "data": result})
                    await _broadcast_mood(result["mood"], result.get("emotion", 0.5))
                except Exception as e:
                    logger.error("process_message error: %s", e)
                    await ws.send_json({"type": "error", "data": str(e)})

            elif msg_type == "audio":
                audio_b64 = msg.get("data", "")
                sample_rate = msg.get("sample_rate", 16000)
                language = msg.get("language", None)

                if not session_id:
                    session_id = msg.get("session_id") or engine.create_session()

                if not audio_b64:
                    await ws.send_json({"type": "error", "data": "No audio data provided"})
                    continue

                try:
                    audio_bytes = base64.b64decode(audio_b64)
                except Exception:
                    await ws.send_json({"type": "error", "data": "Invalid base64 audio data"})
                    continue

                loop = asyncio.get_running_loop()
                try:
                    text = await loop.run_in_executor(
                        None, asr_transcribe, audio_bytes, sample_rate, language
                    )
                except RuntimeError as e:
                    await ws.send_json({"type": "error", "data": f"ASR error: {e}"})
                    continue

                if not text:
                    await ws.send_json({"type": "error", "data": "No speech detected"})
                    continue

                try:
                    result = await loop.run_in_executor(
                        None, engine.process_message, text, session_id
                    )
                    session_id = result["session_id"]
                    await ws.send_json({"type": "done", "data": result})
                    await _broadcast_mood(result["mood"], result.get("emotion", 0.5))
                except Exception as e:
                    logger.error("process_message error: %s", e)
                    await ws.send_json({"type": "error", "data": str(e)})

            elif msg_type == "command":
                cmd = msg.get("command", "")

                if cmd == "subscribe_mood":
                    _mood_subscribers.add(ws)
                    await ws.send_json({"type": "command_result", "command": cmd, "data": "Subscribed to mood updates"})
                else:
                    result_data = _handle_command(cmd, session_id)
                    if isinstance(result_data, str):
                        await ws.send_json({"type": "command_result", "command": cmd, "data": result_data})
                    else:
                        await ws.send_json(result_data)

                if cmd == "new_session":
                    session_id = None

            else:
                await ws.send_json({"type": "error", "data": f"Unknown message type: {msg_type}"})

    except WebSocketDisconnect:
        _mood_subscribers.discard(ws)
        logger.info("WebSocket client disconnected")
    except Exception as e:
        _mood_subscribers.discard(ws)
        logger.error("WebSocket error: %s", e)


async def _broadcast_mood(mood: str, emotion: float) -> None:
    if not _mood_subscribers:
        return
    msg = {"type": "mood_update", "data": {"mood": mood, "emotion": emotion}}
    dead = set()
    for ws in _mood_subscribers:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _mood_subscribers.difference_update(dead)


def _handle_command(cmd: str, session_id: str | None) -> str | dict:
    if cmd == "memory":
        return engine.get_formatted_memory()
    elif cmd == "clear_memory":
        engine.clear_memory()
        return "Memory cleared"
    elif cmd == "new_session":
        sid = engine.create_session()
        return f"New session created: {sid[:8]}..."
    elif cmd == "history":
        if session_id:
            return engine.get_history(session_id)
        return "No active session"
    elif cmd == "sessions":
        recent = engine.get_recent_sessions()
        if not recent:
            return {"type": "command_result", "command": cmd, "data": [], "display": "No sessions found"}
        return {"type": "command_result", "command": cmd, "data": recent}
    else:
        return {"type": "error", "data": f"Unknown command: {cmd}"}


def main() -> None:
    port = 8765
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    logger.info("Starting KuroAPI on 0.0.0.0:%s", port)
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
