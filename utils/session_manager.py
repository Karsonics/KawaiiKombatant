from server.process.storage.mysql_storage import MySQLConversationStorage
import argparse
import json
from datetime import datetime
from typing import List, Optional

from utils.logging import logger


def list_sessions(storage: MySQLConversationStorage) -> None:
    sessions = storage.get_all_sessions()
    print(f"\n=== Found {len(sessions)} sessions ===")
    for session in sessions:
        history = storage.get_conversation_history(session, limit=1)
        if history:
            last_msg = history[-1]
            print(f"\nSession: {session}")
            print(f"Last message: {last_msg['timestamp']}")
            print(f"Preview: {last_msg['content'][:60]}...")


def view_session(storage: MySQLConversationStorage, session_id: str) -> None:
    print(f"\n=== Session: {session_id} ===\n")

    state = storage.get_character_state(session_id)
    if state:
        print(f"Character State:")
        print(f"  Mood: {state['mood']}")
        print(f"  Emotion Level: {state['emotion_level']}")
        print(f"  Last Updated: {state['last_updated']}")
        print()

    history = storage.get_conversation_history(session_id)
    print(f"=== Conversation ({len(history)} messages) ===\n")

    for i, msg in enumerate(history, 1):
        role = msg['role'].upper()
        content = msg['content']
        timestamp = msg['timestamp']

        if role == "SYSTEM":
            print(f"[{i}] SYSTEM (Setup)")
            print(f"    {content[:100]}...")
        else:
            print(f"[{i}] {role} - {timestamp}")
            print(f"    {content}\n")


def clear_session(storage: MySQLConversationStorage, session_id: str) -> None:
    confirm = input(f"Are you sure you want to delete session {session_id}? (yes/no): ")
    if confirm.lower() == 'yes':
        storage.clear_session(session_id)
        print("Session cleared successfully")
    else:
        print("Operation cancelled")


def export_session(storage: MySQLConversationStorage, session_id: str, filename: str, fmt: str = "txt") -> None:
    history = storage.get_conversation_history(session_id)

    if fmt == "json":
        export_data = {
            "session_id": session_id,
            "export_date": datetime.now().isoformat(),
            "messages": [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "timestamp": str(m["timestamp"]),
                }
                for m in history if m["role"] != "system"
            ],
        }
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
        logger.info("Session exported to %s (JSON)", filename)
    else:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("KawaiiKombatant Conversation Export\n")
            f.write(f"Session ID: {session_id}\n")
            f.write(f"Export Date: {datetime.now().isoformat()}\n")
            f.write("=" * 60 + "\n\n")

            for msg in history:
                if msg['role'] != 'system':
                    f.write(f"{msg['role'].upper()} [{msg['timestamp']}]:\n")
                    f.write(f"{msg['content']}\n\n")

        logger.info("Session exported to %s (text)", filename)

    print(f"Session exported to {filename}")


def import_session(storage: MySQLConversationStorage, filename: str) -> None:
    import uuid

    with open(filename, 'r', encoding='utf-8') as f:
        data = json.load(f)

    session_id = str(uuid.uuid4())
    messages = data.get("messages", data.get("conversation", []))

    for msg in messages:
        storage.add_message(
            session_id=session_id,
            role=msg["role"],
            content=msg["content"],
            metadata={"imported": True, "original_session": data.get("session_id", "")},
        )

    print(f"Imported {len(messages)} messages into new session: {session_id[:8]}...")


def main() -> None:
    parser = argparse.ArgumentParser(description="KawaiiKombatant Session Manager")
    parser.add_argument('action', choices=['list', 'view', 'clear', 'export', 'import'],
                       help="Action to perform")
    parser.add_argument('--session', '-s', help="Session ID")
    parser.add_argument('--output', '-o', help="Output filename for export")
    parser.add_argument('--format', '-f', choices=['txt', 'json'], default='txt',
                       help="Export format (default: txt)")
    parser.add_argument('--config', '-c', default="configs/database_config.yaml",
                       help="Database config file path")

    args = parser.parse_args()

    storage = MySQLConversationStorage(args.config)

    try:
        if args.action == 'list':
            list_sessions(storage)

        elif args.action == 'view':
            if not args.session:
                print("Error: --session required for view action")
                return
            view_session(storage, args.session)

        elif args.action == 'clear':
            if not args.session:
                print("Error: --session required for clear action")
                return
            clear_session(storage, args.session)

        elif args.action == 'export':
            if not args.session:
                print("Error: --session required for export action")
                return
            output = args.output or f"session_{args.session[:8]}.{args.format}"
            export_session(storage, args.session, output, args.format)

        elif args.action == 'import':
            if not args.output:
                print("Error: --output required for import (path to JSON file)")
                return
            import_session(storage, args.output)

    finally:
        storage.close()


if __name__ == "__main__":
    main()
