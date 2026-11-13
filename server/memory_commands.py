"""Safe command handling for memory management operations."""

from server.kuro_engine import MemoryClearError


def handle_memory_command(
    engine, cmd, *, scope="legacy", memory_id=None, confirmed=False, logger
) -> dict | None:
    if cmd == "memory_status":
        return {
            "type": "command_result",
            "command": cmd,
            "data": engine.get_memory_status(),
        }
    if cmd == "memory_review":
        return {
            "type": "command_result",
            "command": cmd,
            "data": engine.get_memory_review(),
        }
    if cmd == "confirm_memory":
        if not memory_id:
            return {"type": "error", "data": "memory_id is required"}
        if confirmed is not True:
            return {
                "type": "command_result",
                "command": cmd,
                "data": {
                    "confirmation_required": True,
                    "memory_id": memory_id,
                    "message": (
                        f"Send confirm_memory again with memory_id='{memory_id}' "
                        "and confirm=true to record explicit confirmation."
                    ),
                },
            }
        try:
            return {
                "type": "command_result",
                "command": cmd,
                "data": engine.confirm_markdown_memory(memory_id, confirmed=True),
            }
        except Exception:
            logger.warning("Markdown memory confirmation failed")
            return {"type": "error", "data": "Unable to confirm the requested memory."}
    if cmd == "clear_memory":
        if confirmed is not True:
            return {
                "type": "command_result",
                "command": cmd,
                "data": {
                    "confirmation_required": True,
                    "scope": scope,
                    "message": (
                        f"Send clear_memory again with scope='{scope}' and "
                        "confirm=true to proceed."
                    ),
                },
            }
        try:
            return {
                "type": "command_result",
                "command": cmd,
                "data": engine.clear_memory(scope=scope, confirmed=True),
            }
        except MemoryClearError as exc:
            logger.warning("Memory clear partially failed for scope %s", scope)
            return {"type": "error", "data": exc.user_message}
        except Exception:
            logger.warning("Memory clear failed for scope %s", scope)
            return {"type": "error", "data": "Unable to clear the requested memory."}
    if cmd == "delete_memory":
        if not memory_id:
            return {"type": "error", "data": "memory_id is required"}
        if confirmed is not True:
            return {
                "type": "command_result",
                "command": cmd,
                "data": {
                    "confirmation_required": True,
                    "memory_id": memory_id,
                    "message": (
                        f"Send delete_memory again with memory_id='{memory_id}' "
                        "and confirm=true to proceed."
                    ),
                },
            }
        try:
            return {
                "type": "command_result",
                "command": cmd,
                "data": engine.delete_markdown_memory(memory_id, confirmed=True),
            }
        except Exception:
            logger.warning("Markdown memory deletion failed")
            return {
                "type": "error",
                "data": "Unable to delete the requested Markdown memory.",
            }
    return None
