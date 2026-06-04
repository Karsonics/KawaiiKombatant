from server.process.storage.mysql_storage import MySQLConversationStorage
import json
from typing import Dict, List, Optional, Any

from utils.logging import logger


class UserMemory:
    def __init__(self, storage: MySQLConversationStorage, user_id: str = "default_user") -> None:
        self.storage = storage
        self.user_id = user_id
        self.memory = self._load_memory()
        self._dirty = False
        self._ensure_all_fields()

    def _create_default_memory(self) -> Dict:
        return {
            "personal_info": {},
            "preferences": {},
            "important_facts": [],
            "detailed_preferences": {},
            "conversation_summary": [],
            "topics_discussed": []
        }

    def _ensure_all_fields(self) -> None:
        default_memory = self._create_default_memory()
        modified = False
        for key in default_memory:
            if key not in self.memory:
                self.memory[key] = default_memory[key]
                modified = True

        if modified:
            self.save_memory()

    def _load_memory(self) -> Dict:
        try:
            connection = self.storage._get_connection()
        except Exception:
            return self._create_default_memory()
        cursor = connection.cursor(dictionary=True)

        try:
            table_name = self.storage.config['tables']['user_preferences']
            query = f"SELECT preferences FROM {table_name} WHERE user_id = %s"
            cursor.execute(query, (self.user_id,))
            result = cursor.fetchone()

            if result and result['preferences']:
                try:
                    loaded_memory = json.loads(result['preferences'])
                    default_memory = self._create_default_memory()
                    for key in default_memory:
                        if key not in loaded_memory:
                            loaded_memory[key] = default_memory[key]
                    return loaded_memory
                except json.JSONDecodeError:
                    return self._create_default_memory()
            else:
                return self._create_default_memory()
        finally:
            cursor.close()
            connection.close()

    def save_memory(self) -> None:
        try:
            connection = self.storage._get_connection()
        except Exception:
            return
        cursor = connection.cursor()

        try:
            table_name = self.storage.config['tables']['user_preferences']
            memory_json = json.dumps(self.memory)

            query = f"""
            INSERT INTO {table_name} (user_id, preferences)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE
                preferences = VALUES(preferences),
                updated_at = CURRENT_TIMESTAMP
            """
            cursor.execute(query, (self.user_id, memory_json))
            connection.commit()
        finally:
            cursor.close()
            connection.close()

    def flush(self) -> None:
        if self._dirty:
            self.save_memory()
            self._dirty = False

    def add_preference(self, category: str, value: str, context: str = None) -> None:
        if context:
            if category not in self.memory["detailed_preferences"]:
                self.memory["detailed_preferences"][category] = []

            pref_entry = {"value": value, "context": context}
            if pref_entry not in self.memory["detailed_preferences"][category]:
                self.memory["detailed_preferences"][category].append(pref_entry)
                if len(self.memory["detailed_preferences"][category]) > 5:
                    self.memory["detailed_preferences"][category] = self.memory["detailed_preferences"][category][-5:]
        else:
            self.memory["preferences"][category] = value

        self._dirty = True

    def add_personal_info(self, key: str, value: str) -> None:
        self.memory["personal_info"][key] = value
        self._dirty = True

    def add_fact(self, fact: str, context: str = None) -> bool:
        if context:
            fact_entry = {"fact": fact, "context": context}
        else:
            fact_entry = {"fact": fact}

        existing = [f for f in self.memory["important_facts"]
                   if (isinstance(f, dict) and f.get("fact") == fact) or f == fact]

        if not existing:
            self.memory["important_facts"].append(fact_entry)
            if len(self.memory["important_facts"]) > 30:
                self.memory["important_facts"] = self.memory["important_facts"][-30:]
            self._dirty = True
            return True
        return False

    def add_topic(self, topic: str) -> None:
        if topic not in self.memory["topics_discussed"]:
            self.memory["topics_discussed"].append(topic)
            if len(self.memory["topics_discussed"]) > 30:
                self.memory["topics_discussed"] = self.memory["topics_discussed"][-30:]
            self._dirty = True

    def get_preference(self, category: str) -> Optional[str]:
        return self.memory["preferences"].get(category)

    def get_personal_info(self, key: str) -> Optional[str]:
        return self.memory["personal_info"].get(key)

    def get_memory_summary(self) -> str:
        summary_parts = []

        if self.memory.get("personal_info"):
            info_str = ", ".join([f"{k}: {v}" for k, v in self.memory["personal_info"].items()])
            summary_parts.append(f"Personal info: {info_str}")

        if self.memory.get("preferences"):
            pref_str = ", ".join([f"{k}: {v}" for k, v in self.memory["preferences"].items()])
            summary_parts.append(f"Preferences: {pref_str}")

        if self.memory.get("detailed_preferences"):
            for category, prefs in self.memory["detailed_preferences"].items():
                for pref in prefs:
                    summary_parts.append(f"{category}: {pref['value']} ({pref['context']})")

        if self.memory.get("important_facts"):
            for fact_entry in self.memory["important_facts"][-5:]:
                if isinstance(fact_entry, dict):
                    if fact_entry.get("context"):
                        summary_parts.append(f"{fact_entry['fact']} - {fact_entry['context']}")
                    else:
                        summary_parts.append(fact_entry['fact'])
                else:
                    summary_parts.append(fact_entry)

        return " | ".join(summary_parts) if summary_parts else "No stored memories yet."

    def build_context_prompt(self) -> str:
        if not any([self.memory.get("personal_info"), self.memory.get("preferences"),
                    self.memory.get("detailed_preferences"), self.memory.get("important_facts")]):
            return ""

        context = "\n[MEMORY: Things you remember about this user from previous conversations]\n"

        if self.memory.get("personal_info"):
            context += "Personal Information:\n"
            for key, value in self.memory["personal_info"].items():
                context += f"- {key}: {value}\n"

        if self.memory.get("preferences"):
            context += "Simple Preferences:\n"
            for key, value in self.memory["preferences"].items():
                context += f"- {key}: {value}\n"

        if self.memory.get("detailed_preferences"):
            context += "Detailed Preferences:\n"
            for category, prefs in self.memory["detailed_preferences"].items():
                for pref in prefs:
                    context += f"- {category}: {pref['value']} - {pref['context']}\n"

        if self.memory.get("important_facts"):
            context += "Important Facts:\n"
            for fact_entry in self.memory["important_facts"][-15:]:
                if isinstance(fact_entry, dict):
                    if fact_entry.get("context"):
                        context += f"- {fact_entry['fact']} (Context: {fact_entry['context']})\n"
                    else:
                        context += f"- {fact_entry['fact']}\n"
                else:
                    context += f"- {fact_entry}\n"

        context += "[Remember to naturally reference these memories when relevant, but don't force it!]\n"

        return context

    def clear_memory(self) -> None:
        self.memory = self._create_default_memory()
        self.save_memory()

    def display_memory(self) -> None:
        print("\n=== User Memory ===")
        print(f"User ID: {self.user_id}\n")

        if self.memory.get("personal_info"):
            print("Personal Information:")
            for key, value in self.memory["personal_info"].items():
                print(f"   - {key}: {value}")

        if self.memory.get("preferences"):
            print("\nSimple Preferences:")
            for key, value in self.memory["preferences"].items():
                print(f"   - {key}: {value}")

        if self.memory.get("detailed_preferences"):
            print("\nDetailed Preferences:")
            for category, prefs in self.memory["detailed_preferences"].items():
                print(f"   {category}:")
                for pref in prefs:
                    print(f"      -> {pref['value']} - {pref['context']}")

        if self.memory.get("important_facts"):
            print("\nImportant Facts:")
            for i, fact_entry in enumerate(self.memory["important_facts"], 1):
                if isinstance(fact_entry, dict):
                    if fact_entry.get("context"):
                        print(f"   {i}. {fact_entry['fact']}")
                        print(f"      Context: {fact_entry['context']}")
                    else:
                        print(f"   {i}. {fact_entry['fact']}")
                else:
                    print(f"   {i}. {fact_entry}")

        if self.memory.get("topics_discussed"):
            print(f"\nTopics Discussed ({len(self.memory['topics_discussed'])}):")
            print(f"   {', '.join(self.memory['topics_discussed'][-10:])}")

        print("=" * 50)
