import sys
import os

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Now import will work
from server.process.storage.mysql_storage import MySQLConversationStorage  # noqa: E402

try:
    print("Attempting to connect to MySQL...")
    storage = MySQLConversationStorage("configs/database_config.yaml")
    print("✓ Connected successfully!")
    print("✓ Tables created successfully!")

    # Test listing sessions
    sessions = storage.get_all_sessions()
    print(f"✓ Found {len(sessions)} existing sessions")

    storage.close()
    print("✓ Connection closed properly")
except Exception as e:
    print(f"✗ Error: {e}")
    print("\nTroubleshooting:")
    print("1. Make sure WAMP icon is GREEN")
    print("2. Check database name: kawaii_kombatant")
    print("3. Check username/password in config")
