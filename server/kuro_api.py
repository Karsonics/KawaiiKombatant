import asyncio
import base64
import json
import os
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from server.kuro_engine import KuroEngine
from server.api_listener import parse_api_server_args
from server.api_routes import create_health_handler, register_api_routes
from server.memory_commands import handle_memory_command
from server.process.asr_func.asr_push_to_talk import transcribe_bytes as asr_transcribe
from utils.logging import logger

engine = KuroEngine()
_mood_subscribers: set[WebSocket] = set()
app = FastAPI(title="Kuro V-Tuber Engine")


_web_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "web"


async def web_root():
    return FileResponse(str(_web_dir / "index.html"))


async def web_settings():
    return FileResponse(str(_web_dir / "settings.html"))


@app.on_event("shutdown")
async def shutdown():
    engine.close()


health = create_health_handler(lambda: engine)


async def _process_with_streaming(
    engine: KuroEngine, user_input: str, session_id: str, queue: asyncio.Queue
) -> dict:
    loop = asyncio.get_running_loop()

    def on_sentence(s: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ("sentence", s))

    def on_done(fut) -> None:
        try:
            result = fut.result()
            loop.call_soon_threadsafe(queue.put_nowait, ("result", result))
        except Exception as e:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", e))

    fut = loop.run_in_executor(
        None,
        lambda: engine.process_message(user_input, session_id, sentence_callback=on_sentence),
    )
    fut.add_done_callback(on_done)
    return await fut


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

                queue: asyncio.Queue = asyncio.Queue()

                try:
                    result_fut = asyncio.ensure_future(
                        _process_with_streaming(engine, user_input, session_id, queue)
                    )

                    # Stream sentences as they arrive
                    while True:
                        typ, payload = await queue.get()
                        if typ == "sentence":
                            await ws.send_json({"type": "chunk", "data": payload})
                        elif typ == "result":
                            result = payload
                            break
                        elif typ == "error":
                            raise payload

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
                    await ws.send_json(
                        {"type": "error", "data": "No audio data provided"}
                    )
                    continue

                try:
                    audio_bytes = base64.b64decode(audio_b64)
                except Exception:
                    await ws.send_json(
                        {"type": "error", "data": "Invalid base64 audio data"}
                    )
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

                queue: asyncio.Queue = asyncio.Queue()

                try:
                    result_fut = asyncio.ensure_future(
                        _process_with_streaming(engine, text, session_id, queue)
                    )

                    while True:
                        typ, payload = await queue.get()
                        if typ == "sentence":
                            await ws.send_json({"type": "chunk", "data": payload})
                        elif typ == "result":
                            result = payload
                            break
                        elif typ == "error":
                            raise payload

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
                    await ws.send_json(
                        {
                            "type": "command_result",
                            "command": cmd,
                            "data": "Subscribed to mood updates",
                        }
                    )
                else:
                    result_data = _handle_command(
                        cmd,
                        session_id,
                        scope=msg.get("scope", "legacy"),
                        memory_id=msg.get("memory_id"),
                        confirmed=msg.get("confirm") is True,
                    )
                    if isinstance(result_data, str):
                        await ws.send_json(
                            {
                                "type": "command_result",
                                "command": cmd,
                                "data": result_data,
                            }
                        )
                    else:
                        await ws.send_json(result_data)

                if cmd == "new_session":
                    session_id = None

            else:
                await ws.send_json(
                    {"type": "error", "data": f"Unknown message type: {msg_type}"}
                )

    except WebSocketDisconnect:
        _mood_subscribers.discard(ws)
        logger.info("WebSocket client disconnected")
    except Exception as e:
        _mood_subscribers.discard(ws)
        logger.error("WebSocket error: %s", e)


register_api_routes(
    app,
    _web_dir,
    static_files_factory=StaticFiles,
    logger=logger,
    root_handler=web_root,
    settings_handler=web_settings,
    health_handler=health,
    websocket_handler=websocket_endpoint,
)


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


def _handle_command(
    cmd: str,
    session_id: str | None,
    *,
    scope: str = "legacy",
    memory_id: str | None = None,
    confirmed: bool = False,
) -> str | dict:
    memory_result = handle_memory_command(
        engine,
        cmd,
        scope=scope,
        memory_id=memory_id,
        confirmed=confirmed,
        logger=logger,
    )
    if memory_result is not None:
        return memory_result
    if cmd == "memory":
        return engine.get_formatted_memory()
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
            return {
                "type": "command_result",
                "command": cmd,
                "data": [],
                "display": "No sessions found",
            }
        return {"type": "command_result", "command": cmd, "data": recent}
    else:
        return {"type": "error", "data": f"Unknown command: {cmd}"}


def main() -> None:
    args = parse_api_server_args()
    logger.info("Starting KuroAPI on %s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
