import asyncio
import json
import sys
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

from server.kuro_engine import KuroEngine
from utils.logging import logger

engine = KuroEngine()
app = FastAPI(title="Kuro V-Tuber Engine")


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
                except Exception as e:
                    logger.error("process_message error: %s", e)
                    await ws.send_json({"type": "error", "data": str(e)})

            elif msg_type == "command":
                cmd = msg.get("command", "")
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
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error("WebSocket error: %s", e)


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
            return "No sessions found"
        lines = []
        for s in recent:
            lines.append(f"  [{s['session_id'][:8]}...] {s['timestamp']} - {s['preview']}")
        return "Recent sessions:\n" + "\n".join(lines)
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
