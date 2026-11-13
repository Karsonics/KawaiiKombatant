#!/usr/bin/env python3
import sys
import asyncio
import json

from utils.logging import logger


def run_direct(dry_run: bool = False, voice: str = None) -> None:
    from server.kuro_engine import KuroEngine, MemoryClearError

    engine = KuroEngine(dry_run=dry_run, voice_override=voice)
    engine.display_memory()
    session_id = _pick_session(engine)
    print(
        "\nCommands: 'exit' | 'history' | 'memory' | 'memory_status' | "
        "'memory_review' | 'confirm_memory <id>' | "
        "'clear_memory [legacy|markdown|both]' | 'delete_memory <id>'\n"
    )

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

        if user_input.lower() == "memory_status":
            print(engine.get_memory_status())
            continue

        if user_input.lower() == "memory_review":
            print(engine.get_memory_review())
            continue

        command_parts = user_input.split()
        if command_parts[0].lower() == "clear_memory":
            if len(command_parts) > 2:
                print("Use clear_memory [legacy|markdown|both]")
                continue
            scope = command_parts[1].lower() if len(command_parts) > 1 else "legacy"
            if scope not in {"legacy", "markdown", "both"}:
                print("Use clear_memory [legacy|markdown|both]")
                continue
            confirmed = input(
                f"Clear {scope} memory? Type yes to confirm: "
            ).strip().lower() == "yes"
            if confirmed:
                try:
                    print(engine.clear_memory(scope=scope, confirmed=True))
                except MemoryClearError as exc:
                    print(exc.user_message)
                except Exception:
                    print("Unable to clear the requested memory.")
            continue

        if command_parts[0].lower() == "delete_memory":
            if len(command_parts) != 2:
                print("Use delete_memory <memory-id>")
                continue
            confirmed = input(
                f"Delete Markdown memory {command_parts[1]}? Type yes to confirm: "
            ).strip().lower() == "yes"
            if confirmed:
                try:
                    print(
                        engine.delete_markdown_memory(
                            command_parts[1], confirmed=True
                        )
                    )
                except Exception:
                    print("Unable to delete the requested Markdown memory.")
            continue

        if command_parts[0].lower() == "confirm_memory":
            if len(command_parts) != 2:
                print("Use confirm_memory <memory-id>")
                continue
            confirmed = input(
                f"Confirm Markdown memory {command_parts[1]}? Type yes: "
            ).strip().lower() == "yes"
            if confirmed:
                try:
                    print(
                        engine.confirm_markdown_memory(
                            command_parts[1], confirmed=True
                        )
                    )
                except Exception:
                    print("Unable to confirm the requested memory.")
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
        print(
            "\nCommands: 'exit' | 'history' | 'memory' | 'memory_status' | "
            "'memory_review' | 'confirm_memory <id>' | "
            "'clear_memory [legacy|markdown|both]' | 'delete_memory <id>' | "
            "'new_session'\n"
        )

        while True:
            user_input = input("You: ").strip()
            if not user_input:
                continue

            if user_input.lower() == "exit":
                print("Goodbye!")
                break

            command_parts = user_input.split()
            command_name = command_parts[0].lower()
            if command_name in (
                "history",
                "memory",
                "clear_memory",
                "memory_status",
                "memory_review",
                "confirm_memory",
                "delete_memory",
                "new_session",
                "sessions",
            ):
                payload = {"type": "command", "command": command_name}
                if command_name == "clear_memory":
                    if len(command_parts) > 2:
                        print("Use clear_memory [legacy|markdown|both]")
                        continue
                    scope = (
                        command_parts[1].lower()
                        if len(command_parts) > 1
                        else "legacy"
                    )
                    if scope not in {"legacy", "markdown", "both"}:
                        print("Use clear_memory [legacy|markdown|both]")
                        continue
                    confirmed = input(
                        f"Clear {scope} memory? Type yes to confirm: "
                    ).strip().lower() == "yes"
                    if not confirmed:
                        continue
                    payload.update(scope=scope, confirm=True)
                elif command_name == "delete_memory":
                    if len(command_parts) != 2:
                        print("Use delete_memory <memory-id>")
                        continue
                    payload["memory_id"] = command_parts[1]
                    confirmed = input(
                        f"Delete Markdown memory {command_parts[1]}? "
                        "Type yes to confirm: "
                    ).strip().lower() == "yes"
                    if not confirmed:
                        continue
                    payload["confirm"] = True
                elif command_name == "confirm_memory":
                    if len(command_parts) != 2:
                        print("Use confirm_memory <memory-id>")
                        continue
                    payload["memory_id"] = command_parts[1]
                    confirmed = input(
                        f"Confirm Markdown memory {command_parts[1]}? Type yes: "
                    ).strip().lower() == "yes"
                    if not confirmed:
                        continue
                    payload["confirm"] = True
                await ws.send(json.dumps(payload))
                resp = json.loads(await ws.recv())
                if resp.get("type") == "command_result":
                    print(f"\n{resp['data']}\n")
                    if command_name == "new_session":
                        session_id = None
                elif resp.get("type") == "error":
                    print(f"\n[Error] {resp['data']}\n")
                continue

            await ws.send(
                json.dumps(
                    {
                        "type": "message",
                        "data": user_input,
                        "session_id": session_id,
                    }
                )
            )

            resp = json.loads(await ws.recv())
            rtype = resp.get("type")

            if rtype == "done":
                data = resp["data"]
                print(f"\nKuro: {data['text']}\n")
                session_id = data["session_id"]
            elif rtype == "error":
                print(f"\n[Error] {resp['data']}\n")


async def run_voice(
    host: str = "localhost", port: int = 8765, model: str = "base"
) -> None:
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


def _parse_arg(name: str, default=None):
    for i, arg in enumerate(sys.argv):
        if arg == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if arg.startswith(f"{name}="):
            return arg.split("=", 1)[1]
    return default


def _has_flag(name: str) -> bool:
    return name in sys.argv


def _parse_host_port() -> tuple[str, int]:
    host = _parse_arg("--host", "localhost")
    port = int(_parse_arg("--port", "8765"))
    return host, port


def main() -> None:
    if _has_flag("--avatar") and _has_flag("--voice"):
        print("--avatar and --voice cannot be combined. Use separate terminals.")
        return

    if _has_flag("--dry-run"):
        print("\n=== DRY RUN MODE ===")
        print("Config will be validated, no LLM or DB connections made.\n")
        voice = _parse_arg("--voice")
        run_direct(dry_run=True, voice=voice)
        return

    if _has_flag("--avatar"):
        host, port = _parse_host_port()
        asyncio.run(run_avatar(host, port))

    elif _has_flag("--voice"):
        host, port = _parse_host_port()
        model = _parse_arg("--model", "base")
        asyncio.run(run_voice(host, port, model))

    elif _has_flag("--ws"):
        host, port = _parse_host_port()
        asyncio.run(run_ws(host, port))

    else:
        voice = _parse_arg("--voice")
        run_direct(voice=voice)


if __name__ == "__main__":
    main()
