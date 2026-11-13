from mysql.connector import Error, pooling
from typing import List, Dict, Optional
import yaml
import json

from utils.logging import logger
from utils.retry import retry


class MySQLConversationStorage:
    def __init__(self, config_path: str = "configs/database_config.yaml") -> None:
        self.config = self._load_config(config_path)
        self.connection_pool: Optional[pooling.MySQLConnectionPool] = None
        self._create_connection_pool()
        self._initialize_database()

    def _load_config(self, config_path: str) -> dict:
        with open(config_path, "r") as f:
            return yaml.safe_load(f)

    def _create_connection_pool(self) -> None:
        try:
            mysql_config = self.config["mysql"]
            connection_config = self.config["connection"]

            self.connection_pool = pooling.MySQLConnectionPool(
                pool_name="kawaii_pool",
                pool_size=connection_config["max_connections"],
                pool_reset_session=True,
                host=mysql_config["host"],
                port=mysql_config["port"],
                database=mysql_config["database"],
                user=mysql_config["username"],
                password=mysql_config["password"],
            )
            logger.info("MySQL connection pool created successfully")
        except Error as e:
            logger.error("Error creating connection pool: %s", e)
            raise

    def _get_connection(self):
        try:
            return self.connection_pool.get_connection()
        except Error as e:
            logger.error("Error getting connection: %s", e)
            raise

    def _initialize_database(self) -> None:
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()

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
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
            cursor.execute(conversations_table)

            character_states_table = f"""
            CREATE TABLE IF NOT EXISTS {self.config['tables']['character_states']} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(50) NOT NULL,
                mood VARCHAR(50),
                emotion_level FLOAT,
                context_summary TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY unique_session (session_id(50))
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
            cursor.execute(character_states_table)

            user_preferences_table = f"""
            CREATE TABLE IF NOT EXISTS {self.config['tables']['user_preferences']} (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(50) NOT NULL,
                preferences TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY unique_user (user_id(50))
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
            cursor.execute(user_preferences_table)

            session_summaries = f"""
            CREATE TABLE IF NOT EXISTS {self._session_summaries_table()} (
                session_id VARCHAR(50) NOT NULL PRIMARY KEY,
                summary TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
            cursor.execute(session_summaries)

            connection.commit()
            logger.info("Database tables initialized successfully")

        except Error as e:
            logger.error("Error initializing database: %s", e)
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    @retry(max_attempts=3, delay=2, backoff=2)
    def add_message(
        self, session_id: str, role: str, content: str, metadata: Optional[Dict] = None
    ) -> int:
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()

            table_name = self.config["tables"]["conversations"]
            metadata_json = json.dumps(metadata) if metadata else None

            query = f"""
            INSERT INTO {table_name} (session_id, role, content, metadata)
            VALUES (%s, %s, %s, %s)
            """
            cursor.execute(query, (session_id, role, content, metadata_json))
            connection.commit()

            return cursor.lastrowid

        except Error as e:
            logger.error("Error adding message: %s", e)
            if connection:
                connection.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def get_conversation_history(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[Dict]:
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor(dictionary=True)

            table_name = self.config["tables"]["conversations"]

            if limit:
                query = f"""
                SELECT id, role, content, timestamp, metadata
                FROM {table_name}
                WHERE session_id = %s
                ORDER BY timestamp DESC, id DESC
                LIMIT %s
                """
                cursor.execute(query, (session_id, limit))
            else:
                query = f"""
                SELECT id, role, content, timestamp, metadata
                FROM {table_name}
                WHERE session_id = %s
                ORDER BY timestamp ASC, id ASC
                """
                cursor.execute(query, (session_id,))

            results = cursor.fetchall()

            for result in results:
                if result["metadata"]:
                    try:
                        result["metadata"] = json.loads(result["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        result["metadata"] = None

            if limit:
                results.reverse()

            return results

        except Error as e:
            logger.error("Error retrieving conversation history: %s", e)
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def get_recent_context(
        self, session_id: str, context_length: int = 10
    ) -> List[Dict]:
        return self.get_conversation_history(session_id, limit=context_length)

    def get_messages_after(
        self, session_id: str, message_id: int
    ) -> List[Dict]:
        """Return messages after a stable checkpoint in chronological order."""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor(dictionary=True)
            table_name = self.config["tables"]["conversations"]
            cursor.execute(
                f"""
                SELECT id, role, content, timestamp, metadata
                FROM {table_name}
                WHERE session_id = %s AND id > %s
                ORDER BY timestamp ASC, id ASC
                """,
                (session_id, message_id),
            )
            results = cursor.fetchall()
            for result in results:
                if result["metadata"]:
                    try:
                        result["metadata"] = json.loads(result["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        result["metadata"] = None
            return results
        except Error as e:
            logger.error("Error retrieving messages after summary checkpoint: %s", e)
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def update_character_state(
        self, session_id: str, mood: str, emotion_level: float, context_summary: str
    ) -> None:
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()

            table_name = self.config["tables"]["character_states"]

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
            logger.error("Error updating character state: %s", e)
            if connection:
                connection.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def get_character_state(self, session_id: str) -> Optional[Dict]:
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor(dictionary=True)

            table_name = self.config["tables"]["character_states"]

            query = f"""
            SELECT mood, emotion_level, context_summary, last_updated
            FROM {table_name}
            WHERE session_id = %s
            """
            cursor.execute(query, (session_id,))

            return cursor.fetchone()

        except Error as e:
            logger.error("Error retrieving character state: %s", e)
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def clear_session(self, session_id: str) -> None:
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()

            cursor.execute(
                f"DELETE FROM {self.config['tables']['conversations']} WHERE session_id = %s",
                (session_id,),
            )
            cursor.execute(
                f"DELETE FROM {self.config['tables']['character_states']} WHERE session_id = %s",
                (session_id,),
            )
            cursor.execute(
                f"DELETE FROM {self._session_summaries_table()} WHERE session_id = %s",
                (session_id,),
            )

            connection.commit()
            logger.info("Session %s cleared successfully", session_id)

        except Error as e:
            logger.error("Error clearing session: %s", e)
            if connection:
                connection.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def _session_summaries_table(self) -> str:
        """Return the configured summary table, with a backward-compatible default."""
        return self.config.get("tables", {}).get(
            "session_summaries", "session_summaries"
        )

    def get_session_summary(self, session_id: str) -> Optional[str]:
        state = self.get_session_summary_state(session_id)
        return state["summary"]

    def get_session_summary_state(self, session_id: str) -> dict:
        """Read a summary and checkpoint, treating old plain text as checkpoint 0."""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            cursor.execute(
                f"SELECT summary FROM {self._session_summaries_table()} "
                "WHERE session_id = %s",
                (session_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return {
                    "summary": None,
                    "last_message_id": 0,
                    "storage_value": None,
                    "row_exists": False,
                }
            raw_summary = row[0]
            if raw_summary is None:
                return {
                    "summary": None,
                    "last_message_id": 0,
                    "storage_value": None,
                    "row_exists": True,
                }
            try:
                envelope = json.loads(raw_summary)
            except (json.JSONDecodeError, TypeError):
                envelope = None
            if (
                isinstance(envelope, dict)
                and envelope.get("format") == "kuro-session-summary-v1"
                and isinstance(envelope.get("summary"), str)
                and type(envelope.get("last_message_id")) is int
                and envelope["last_message_id"] >= 0
            ):
                return {
                    "summary": envelope["summary"],
                    "last_message_id": envelope["last_message_id"],
                    "storage_value": raw_summary,
                    "row_exists": True,
                }
            return {
                "summary": raw_summary,
                "last_message_id": 0,
                "storage_value": raw_summary,
                "row_exists": True,
            }
        except Error as e:
            logger.error("Error retrieving session summary: %s", e)
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def set_session_summary(self, session_id: str, summary: str) -> None:
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            cursor.execute(
                f"""
                INSERT INTO {self._session_summaries_table()} (session_id, summary)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE
                    summary = VALUES(summary),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (session_id, summary),
            )
            connection.commit()
        except Error as e:
            logger.error("Error saving session summary: %s", e)
            if connection:
                connection.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def set_session_summary_state(
        self, session_id: str, summary: str, last_message_id: int
    ) -> None:
        """Persist cumulative summary state in the existing TEXT column."""
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("A non-empty session summary is required")
        if type(last_message_id) is not int or last_message_id < 0:
            raise ValueError("Summary checkpoint must be a non-negative message ID")
        envelope = json.dumps(
            {
                "format": "kuro-session-summary-v1",
                "summary": summary,
                "last_message_id": last_message_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.set_session_summary(session_id, envelope)

    def compare_and_set_session_summary_state(
        self,
        session_id: str,
        summary: str,
        last_message_id: int,
        expected_storage_value: Optional[str],
        *,
        expected_row_exists: Optional[bool] = None,
    ) -> bool:
        """Save only if the exact previously-read row value is still current.

        An absent row is inserted normally; a competing first insert is reported
        as a conflict. Comparing the raw TEXT value also supports legacy rows.
        """
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("A non-empty session summary is required")
        if type(last_message_id) is not int or last_message_id < 0:
            raise ValueError("Summary checkpoint must be a non-negative message ID")
        if expected_row_exists is None:
            expected_row_exists = expected_storage_value is not None
        if type(expected_row_exists) is not bool:
            raise ValueError("Expected row existence must be a boolean")
        if expected_storage_value is not None and not isinstance(expected_storage_value, str):
            raise ValueError("Expected summary state must be text or None")
        if not expected_row_exists and expected_storage_value is not None:
            raise ValueError("A missing summary row cannot have a stored value")
        previous_checkpoint = 0
        if expected_storage_value is not None:
            try:
                previous_envelope = json.loads(expected_storage_value)
            except (json.JSONDecodeError, TypeError):
                previous_envelope = None
            if (
                isinstance(previous_envelope, dict)
                and previous_envelope.get("format") == "kuro-session-summary-v1"
                and type(previous_envelope.get("last_message_id")) is int
                and previous_envelope["last_message_id"] >= 0
            ):
                previous_checkpoint = previous_envelope["last_message_id"]
        if last_message_id < previous_checkpoint:
            return False

        envelope = json.dumps(
            {
                "format": "kuro-session-summary-v1",
                "summary": summary,
                "last_message_id": last_message_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            table = self._session_summaries_table()
            if not expected_row_exists:
                cursor.execute(
                    f"INSERT INTO {table} (session_id, summary) VALUES (%s, %s)",
                    (session_id, envelope),
                )
            elif expected_storage_value is None:
                cursor.execute(
                    f"UPDATE {table} SET summary = %s, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE session_id = %s AND summary IS NULL",
                    (envelope, session_id),
                )
            else:
                cursor.execute(
                    f"UPDATE {table} SET summary = %s, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE session_id = %s AND BINARY summary = BINARY %s",
                    (envelope, session_id, expected_storage_value),
                )
            saved = cursor.rowcount == 1
            connection.commit()
            return saved
        except Error as exc:
            if connection:
                connection.rollback()
            if not expected_row_exists and getattr(exc, "errno", None) == 1062:
                return False
            logger.error("Error comparing and saving session summary state")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def get_all_sessions(self) -> List[str]:
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()

            table_name = self.config["tables"]["conversations"]
            query = f"SELECT DISTINCT session_id FROM {table_name}"
            cursor.execute(query)

            return [row[0] for row in cursor.fetchall()]

        except Error as e:
            logger.error("Error retrieving sessions: %s", e)
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def get_message_count(self, session_id: str) -> int:
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            table_name = self.config["tables"]["conversations"]
            query = f"SELECT COUNT(*) FROM {table_name} WHERE session_id = %s"
            cursor.execute(query, (session_id,))
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error("Error counting messages: %s", e)
            return 0
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def close(self) -> None:
        if self.connection_pool:
            logger.info("MySQL storage closed")
