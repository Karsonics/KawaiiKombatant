import mysql.connector
from mysql.connector import Error, pooling
from datetime import datetime
from typing import List, Dict, Optional
import yaml
import json

class MySQLConversationStorage:
    def __init__(self, config_path: str = "configs/database_config.yaml"):
        """Initialize MySQL connection pool and setup tables"""
        self.config = self._load_config(config_path)
        self.connection_pool = None
        self._create_connection_pool()
        self._initialize_database()
    
    def _load_config(self, config_path: str) -> dict:
        """Load database configuration from YAML file"""
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    
    def _create_connection_pool(self):
        """Create a connection pool for efficient database access"""
        try:
            mysql_config = self.config['mysql']
            connection_config = self.config['connection']
            
            self.connection_pool = pooling.MySQLConnectionPool(
                pool_name="kawaii_pool",
                pool_size=connection_config['max_connections'],
                pool_reset_session=True,
                host=mysql_config['host'],
                port=mysql_config['port'],
                database=mysql_config['database'],
                user=mysql_config['username'],
                password=mysql_config['password']
            )
            print("✓ MySQL connection pool created successfully")
        except Error as e:
            print(f"✗ Error creating connection pool: {e}")
            raise
    
    def _get_connection(self):
        """Get a connection from the pool"""
        try:
            return self.connection_pool.get_connection()
        except Error as e:
            print(f"✗ Error getting connection: {e}")
            raise
    
    def _initialize_database(self):
        """Create necessary tables if they don't exist"""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            
            # Create conversations table
            conversations_table = f"""
            CREATE TABLE IF NOT EXISTS {self.config['tables']['conversations']} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(50) NOT NULL,
                role ENUM('system', 'user', 'assistant') NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT,
                INDEX idx_session (session_id(50)),
                INDEX idx_timestamp (timestamp)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci
            """
            cursor.execute(conversations_table)
            
            # Create character states table
            character_states_table = f"""
            CREATE TABLE IF NOT EXISTS {self.config['tables']['character_states']} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(50) NOT NULL,
                mood VARCHAR(50),
                emotion_level FLOAT,
                context_summary TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY unique_session (session_id(50))
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci
            """
            cursor.execute(character_states_table)
            
            # Create user preferences table
            user_preferences_table = f"""
            CREATE TABLE IF NOT EXISTS {self.config['tables']['user_preferences']} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(50) NOT NULL,
                preferences TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY unique_user (user_id(50))
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci
            """
            cursor.execute(user_preferences_table)
            
            connection.commit()
            print("✓ Database tables initialized successfully")
            
        except Error as e:
            print(f"✗ Error initializing database: {e}")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
    
    def add_message(self, session_id: str, role: str, content: str, metadata: Optional[Dict] = None):
        """Add a single message to the conversation history"""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            
            table_name = self.config['tables']['conversations']
            metadata_json = json.dumps(metadata) if metadata else None
            
            query = f"""
            INSERT INTO {table_name} (session_id, role, content, metadata)
            VALUES (%s, %s, %s, %s)
            """
            cursor.execute(query, (session_id, role, content, metadata_json))
            connection.commit()
            
            return cursor.lastrowid
            
        except Error as e:
            print(f"✗ Error adding message: {e}")
            if connection:
                connection.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
    
    def get_conversation_history(self, session_id: str, limit: Optional[int] = None) -> List[Dict]:
        """Retrieve conversation history for a session"""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor(dictionary=True)
            
            table_name = self.config['tables']['conversations']
            
            if limit:
                query = f"""
                SELECT role, content, timestamp, metadata
                FROM {table_name}
                WHERE session_id = %s
                ORDER BY timestamp DESC
                LIMIT %s
                """
                cursor.execute(query, (session_id, limit))
            else:
                query = f"""
                SELECT role, content, timestamp, metadata
                FROM {table_name}
                WHERE session_id = %s
                ORDER BY timestamp ASC
                """
                cursor.execute(query, (session_id,))
            
            results = cursor.fetchall()
            
            # Parse metadata from TEXT (stored as JSON string)
            for result in results:
                if result['metadata']:
                    try:
                        result['metadata'] = json.loads(result['metadata'])
                    except (json.JSONDecodeError, TypeError):
                        result['metadata'] = None
            
            # Reverse if we used LIMIT (to get most recent first, then reverse to chronological)
            if limit:
                results.reverse()
            
            return results
            
        except Error as e:
            print(f"✗ Error retrieving conversation history: {e}")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
    
    def get_recent_context(self, session_id: str, context_length: int = 10) -> List[Dict]:
        """Get recent messages for context window"""
        return self.get_conversation_history(session_id, limit=context_length)
    
    def update_character_state(self, session_id: str, mood: str, emotion_level: float, context_summary: str):
        """Update or insert character state for a session"""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            
            table_name = self.config['tables']['character_states']
            
            query = f"""
            INSERT INTO {table_name} (session_id, mood, emotion_level, context_summary)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                mood = VALUES(mood),
                emotion_level = VALUES(emotion_level),
                context_summary = VALUES(context_summary),
                last_updated = CURRENT_TIMESTAMP
            """
            cursor.execute(query, (session_id, mood, emotion_level, context_summary))
            connection.commit()
            
        except Error as e:
            print(f"✗ Error updating character state: {e}")
            if connection:
                connection.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
    
    def get_character_state(self, session_id: str) -> Optional[Dict]:
        """Retrieve character state for a session"""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor(dictionary=True)
            
            table_name = self.config['tables']['character_states']
            
            query = f"""
            SELECT mood, emotion_level, context_summary, last_updated
            FROM {table_name}
            WHERE session_id = %s
            """
            cursor.execute(query, (session_id,))
            
            return cursor.fetchone()
            
        except Error as e:
            print(f"✗ Error retrieving character state: {e}")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
    
    def clear_session(self, session_id: str):
        """Clear all data for a specific session"""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            
            # Clear conversations
            cursor.execute(f"DELETE FROM {self.config['tables']['conversations']} WHERE session_id = %s", (session_id,))
            
            # Clear character state
            cursor.execute(f"DELETE FROM {self.config['tables']['character_states']} WHERE session_id = %s", (session_id,))
            
            connection.commit()
            print(f"✓ Session {session_id} cleared successfully")
            
        except Error as e:
            print(f"✗ Error clearing session: {e}")
            if connection:
                connection.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
    
    def get_all_sessions(self) -> List[str]:
        """Get list of all session IDs"""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            
            table_name = self.config['tables']['conversations']
            query = f"SELECT DISTINCT session_id FROM {table_name}"
            cursor.execute(query)
            
            return [row[0] for row in cursor.fetchall()]
            
        except Error as e:
            print(f"✗ Error retrieving sessions: {e}")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
    
    def close(self):
        """Close the connection pool"""
        if self.connection_pool:
            # Connection pools don't have a direct close method
            # Connections are returned to pool automatically
            print("✓ MySQL storage closed")