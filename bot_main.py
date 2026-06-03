#!/usr/bin/env python3
import sys
import asyncio
import json
import os

from utils.logging import logger


def run_direct() -> None:
    from server.kuro_engine import KuroEngine

    engine = KuroEngine()
    engine.display_memory()
    session_id = _pick_session(engine)
    print(f"\nCommands: 'exit' | 'history' | 'memory' | 'clear_memory'\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue

        if user_input.lower() == "exit":
            print(f"Goodbye! Session saved: {session_id[:8]}...")
            engine.close()
            break

        if user_input.lower() == "history":
            print(f"\n{engine.get_history(session_id)}\n")
            continue

        if user_input.lower() == "memory":
            engine.display_memory()
            continue

        if user_input.lower() == "clear_memory":
            if input("Clear ALL user memory? (yes/no): ").lower() == "yes":
                engine.clear_memory()
                print("Memory cleared")
            continue

        try:
            result = engine.process_message(user_input, session_id)
            print(f"\n{engine.character_name}: {result['text']}\n")
            session_id = result["session_id"]
        except Exception as e:
            logger.error("Error: %s", e)
            print(f"\n[Error] {e}\n")


def _pick_session(engine) -> str:
    recent = engine.get_recent_sessions()
    if recent:
        print("\n=== Recent Sessions ===")
        for i, s in enumerate(recent, 1):
            role_icon = "U" if s["role"] == "user" else "K"
            print(f"{i}. [{s['session_id'][:8]}...] {s['timestamp']}")
            print(f"   {role_icon} {s['preview']}")
        print("\n0. Start NEW session")
        choice = input("\nResume session (0 for new): ").strip()
        try:
            choice = int(choice)
            if choice == 0:
                return engine.create_session()
            elif 1 <= choice <= len(recent):
                sid = recent[choice - 1]["session_id"]
                print(f"\nResuming: {sid[:8]}...")
                return sid
        except ValueError:
            pass
    return engine.create_session()


async def run_ws(host: str = "localhost", port: int = 8765) -> None:
    import websockets

    uri = f"ws://{host}:{port}/ws"
    print(f"Connecting to KuroAPI at {uri}")

    try:
        ws = await websockets.connect(uri)
    except (ConnectionRefusedError, OSError) as e:
        print(f"\n[Error] Cannot connect to KuroAPI at {uri}")
        print(f"  {e}")
        print("  Start the server with: python -m server.kuro_api\n")
        return

    async with ws:
        session_id = None
        print("\nCommands: 'exit' | 'history' | 'memory' | 'clear_memory' | 'new_session'\n")

        while True:
            user_input = input("You: ").strip()
            if not user_input:
                continue

            if user_input.lower() == "exit":
                print("Goodbye!")
                break

            if user_input.lower() in ("history", "memory", "clear_memory",
                                       "new_session", "sessions"):
                await ws.send(json.dumps({
                    "type": "command",
                    "command": user_input.lower(),
                }))
                resp = json.loads(await ws.recv())
                if resp.get("type") == "command_result":
                    print(f"\n{resp['data']}\n")
                    if user_input.lower() == "new_session":
                        session_id = None
                elif resp.get("type") == "error":
                    print(f"\n[Error] {resp['data']}\n")
                continue

            await ws.send(json.dumps({
                "type": "message",
                "data": user_input,
                "session_id": session_id,
            }))

            resp = json.loads(await ws.recv())
            rtype = resp.get("type")

            if rtype == "done":
                data = resp["data"]
                print(f"\nKuro: {data['text']}\n")
                session_id = data["session_id"]
            elif rtype == "error":
                print(f"\n[Error] {resp['data']}\n")


async def run_voice(host: str = "localhost", port: int = 8765,
                    model: str = "base") -> None:
    from voice.voice_client import VoiceClient
    client = VoiceClient(
        ws_url=f"ws://{host}:{port}/ws",
        model_size=model,
    )
    await client.run()


async def run_avatar(host: str = "localhost", port: int = 8765) -> None:
    from avatar.avatar_client import AvatarClient
    client = AvatarClient(host=host, port=port)
    await client.run()


def _parse_host_port() -> tuple[str, int]:
    host = "localhost"
    port = 8765
    for i, arg in enumerate(sys.argv):
        if arg == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
        if arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])
    return host, port


def main() -> None:
    if "--avatar" in sys.argv and "--voice" in sys.argv:
        print("--avatar and --voice cannot be combined. Use separate terminals.")
        return

    if "--avatar" in sys.argv:
        host, port = _parse_host_port()
        asyncio.run(run_avatar(host, port))

    elif "--voice" in sys.argv:
        host, port = _parse_host_port()
        model = "base"
        for i, arg in enumerate(sys.argv):
            if arg == "--model" and i + 1 < len(sys.argv):
                model = sys.argv[i + 1]
        asyncio.run(run_voice(host, port, model))

    elif "--ws" in sys.argv:
        host, port = _parse_host_port()
        asyncio.run(run_ws(host, port))

    else:
        run_direct()


if __name__ == "__main__":
    main()
