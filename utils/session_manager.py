"""
Session Manager Utility
Manage conversation sessions stored in MySQL
"""

from server.process.storage.mysql_storage import MySQLConversationStorage
import argparse
from datetime import datetime

def list_sessions(storage):
    """List all available sessions"""
    sessions = storage.get_all_sessions()
    print(f"\n=== Found {len(sessions)} sessions ===")
    for session in sessions:
        history = storage.get_conversation_history(session, limit=1)
        if history:
            last_msg = history[-1]
            print(f"\nSession: {session}")
            print(f"Last message: {last_msg['timestamp']}")
            print(f"Preview: {last_msg['content'][:60]}...")

def view_session(storage, session_id):
    """View full conversation history for a session"""
    print(f"\n=== Session: {session_id} ===\n")
    
    # Get character state
    state = storage.get_character_state(session_id)
    if state:
        print(f"Character State:")
        print(f"  Mood: {state['mood']}")
        print(f"  Emotion Level: {state['emotion_level']}")
        print(f"  Last Updated: {state['last_updated']}")
        print()
    
    # Get conversation history
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

def clear_session(storage, session_id):
    """Clear a specific session"""
    confirm = input(f"Are you sure you want to delete session {session_id}? (yes/no): ")
    if confirm.lower() == 'yes':
        storage.clear_session(session_id)
        print("✓ Session cleared successfully")
    else:
        print("Operation cancelled")

def export_session(storage, session_id, filename):
    """Export session to a text file"""
    history = storage.get_conversation_history(session_id)
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"KawaiiKombatant Conversation Export\n")
        f.write(f"Session ID: {session_id}\n")
        f.write(f"Export Date: {datetime.now().isoformat()}\n")
        f.write("=" * 60 + "\n\n")
        
        for msg in history:
            if msg['role'] != 'system':
                f.write(f"{msg['role'].upper()} [{msg['timestamp']}]:\n")
                f.write(f"{msg['content']}\n\n")
    
    print(f"✓ Session exported to {filename}")

def main():
    parser = argparse.ArgumentParser(description="KawaiiKombatant Session Manager")
    parser.add_argument('action', choices=['list', 'view', 'clear', 'export'],
                       help="Action to perform")
    parser.add_argument('--session', '-s', help="Session ID")
    parser.add_argument('--output', '-o', help="Output filename for export")
    parser.add_argument('--config', '-c', default="configs/database_config.yaml",
                       help="Database config file path")
    
    args = parser.parse_args()
    
    # Initialize storage
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
            output = args.output or f"session_{args.session[:8]}.txt"
            export_session(storage, args.session, output)
    
    finally:
        storage.close()

if __name__ == "__main__":
    main()