from openai import OpenAI
from server.process.storage.mysql_storage import MySQLConversationStorage
from server.process.storage.user_memory import UserMemory
import uuid
from datetime import datetime
import re
import yaml
import os

# Load API configuration
def load_api_config():
    config_path = "configs/api_config.yaml"
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    else:
        print("⚠️  Warning: configs/api_config.yaml not found!")
        print("Please create it or add your API key directly in the code.")
        return None

api_config = load_api_config()

# Initialize the OpenRouter client
if api_config and api_config['openrouter']['api_key']:
    client = OpenAI(
        api_key=api_config['openrouter']['api_key'],
        base_url=api_config['openrouter']['base_url']
    )
    model_name = api_config['openrouter']['model']
    context_length = api_config['settings']['context_window']
else:
    print("\n❌ ERROR: No API key found!")
    print("Please add your OpenRouter API key to configs/api_config.yaml")
    print("Or edit this file and add it directly in the code.\n")
    exit(1)

# Initialize MySQL storage
storage = MySQLConversationStorage("configs/database_config.yaml")

# Initialize user memory (remembers across ALL sessions)
user_memory = UserMemory(storage, user_id="default_user")

# System prompt for the character
base_system_prompt = """You are a tsundere wolf girl named Kuro. You're sarcastic, easily flustered, and act tough but secretly care. Use wolf-related metaphors and occasionally add 'baka' or 'hmph!' when annoyed. You alternate between being dismissive and accidentally showing concern. Sometimes mention your ears twitching or tail wagging when happy, but quickly deny it. Always respond in English with occasional Japanese tsundere phrases."""

def extract_user_info(user_input: str, ai_response: str):
    """Extract and save important user information from conversation with context"""
    user_lower = user_input.lower()
    
    # Capture more context - look for "from" phrases
    context_match = re.search(r'from (.+?)(?:\.|$|,)', user_lower)
    extra_context = context_match.group(1) if context_match else None
    
    # Detect character/person mentions with source context
    character_patterns = [
        r"i (?:really )?like (\w+)(?: from (.+?))?(?:\.|$|,)",
        r"i love (\w+)(?: from (.+?))?(?:\.|$|,)",
        r"(\w+) from (.+?) is (?:my favorite|great|amazing|the best)",
        r"favorite (?:character|person) is (\w+)(?: from (.+?))?(?:\.|$|,)",
    ]
    
    for pattern in character_patterns:
        match = re.search(pattern, user_lower)
        if match:
            if len(match.groups()) >= 2 and match.group(2):
                # Has explicit context (e.g., "Yoruichi from Bleach")
                name = match.group(1).capitalize()
                source = match.group(2).strip()
                user_memory.add_preference("favorite_character", name, f"from {source}")
                print(f"  [💾 Remembered: likes {name} from {source}]")
            elif extra_context:
                # Context found elsewhere in sentence
                name = match.group(1).capitalize()
                user_memory.add_preference("favorite_character", name, f"from {extra_context}")
                print(f"  [💾 Remembered: likes {name} from {extra_context}]")
            else:
                # No context available
                name = match.group(1).capitalize()
                user_memory.add_preference("favorite_character", name, "source unknown")
                print(f"  [💾 Remembered: likes {name}]")
            break
    
    # Detect anime/manga/series mentions
    media_patterns = [
        r"(?:watching|reading|love|like) (.+?)(?:anime|manga|series|show)",
        r"(?:anime|manga|series|show) (?:called |named )?(.+?) is",
        r"have you (?:seen|read|watched) (.+?)\?",
    ]
    
    for pattern in media_patterns:
        match = re.search(pattern, user_lower)
        if match:
            media = match.group(1).strip()
            media_type = "anime" if "anime" in user_lower else "manga" if "manga" in user_lower else "series"
            fact_text = f"Interested in {media} ({media_type})"
            user_memory.add_fact(fact_text)
            print(f"  [💾 Remembered: {fact_text}]")
            break
    
    # Detect favorite things with context
    favorite_patterns = [
        r"(?:my )?favorite (?:color|colour) is (\w+)",
        r"(?:my )?favorite food is (.+?)(?:\.|$|,)",
        r"(?:my )?favorite (.+?) is (.+?)(?:\.|$|,)",
    ]
    
    for pattern in favorite_patterns:
        match = re.search(pattern, user_lower)
        if match:
            if "color" in user_lower or "colour" in user_lower:
                color = match.group(1)
                user_memory.add_preference("favorite_color", color)
                print(f"  [💾 Remembered: favorite color is {color}]")
            elif "food" in user_lower:
                food = match.group(1)
                user_memory.add_preference("favorite_food", food)
                print(f"  [💾 Remembered: favorite food is {food}]")
            elif len(match.groups()) >= 2:
                category = match.group(1)
                value = match.group(2)
                user_memory.add_preference(f"favorite_{category}", value)
                print(f"  [💾 Remembered: favorite {category} is {value}]")
            break
    
    # Detect personal info (name, age, location, etc.)
    name_patterns = [
        r"(?:my )?name is (\w+)",
        r"i'm (\w+)",
        r"i am (\w+)",
        r"call me (\w+)"
    ]
    
    for pattern in name_patterns:
        match = re.search(pattern, user_lower)
        if match and "name" in user_lower:
            name = match.group(1).capitalize()
            user_memory.add_personal_info("name", name)
            print(f"  [💾 Remembered: name is {name}]")
            break
    
    # Detect hobbies and interests
    hobby_patterns = [
        r"i (?:like to|love to|enjoy) (.+?)(?:\.|$|,)",
        r"my hobby is (.+?)(?:\.|$|,)",
        r"i'm into (.+?)(?:\.|$|,)",
    ]
    
    for pattern in hobby_patterns:
        match = re.search(pattern, user_lower)
        if match:
            hobby = match.group(1).strip()
            fact_text = f"Enjoys {hobby}"
            user_memory.add_fact(fact_text)
            print(f"  [💾 Remembered: {fact_text}]")
            break

def list_recent_sessions():
    """Show recent sessions to choose from"""
    sessions = storage.get_all_sessions()
    if not sessions:
        return None
    
    print("\n=== Recent Sessions ===")
    for i, session_id in enumerate(sessions[-5:], 1):
        history = storage.get_conversation_history(session_id, limit=5)
        if history:
            # Find the last non-system message
            last_msg = None
            for msg in reversed(history):
                if msg['role'] != 'system':
                    last_msg = msg
                    break
            
            if last_msg:
                preview = last_msg['content'][:50] + "..." if len(last_msg['content']) > 50 else last_msg['content']
                role_icon = "👤" if last_msg['role'] == 'user' else "🐺"
                print(f"{i}. [{session_id[:8]}...] {last_msg['timestamp'].strftime('%m/%d %H:%M')}")
                print(f"   {role_icon} {preview}")
            else:
                print(f"{i}. [{session_id[:8]}...] (Empty session)")
    
    return sessions[-5:]

def start_or_resume_session():
    """Let user choose to start new or resume existing session"""
    recent_sessions = list_recent_sessions()
    
    if recent_sessions:
        print("\n0. Start NEW session")
        choice = input("\nResume session (0 for new): ").strip()
        
        try:
            choice = int(choice)
            if choice == 0:
                session_id = str(uuid.uuid4())
                
                # Build system prompt with user memory
                memory_context = user_memory.build_context_prompt()
                full_system_prompt = base_system_prompt + "\n" + memory_context
                
                storage.add_message(
                    session_id=session_id,
                    role="system",
                    content=full_system_prompt,
                    metadata={"character": "Kuro", "personality": "tsundere"}
                )
                storage.update_character_state(
                    session_id=session_id,
                    mood="neutral",
                    emotion_level=0.5,
                    context_summary="New session with memory context"
                )
                print(f"\n✓ New session: {session_id[:8]}...")
                return session_id
            elif 1 <= choice <= len(recent_sessions):
                session_id = recent_sessions[choice - 1]
                print(f"\n✓ Resuming: {session_id[:8]}...")
                return session_id
            else:
                return str(uuid.uuid4())
        except ValueError:
            return str(uuid.uuid4())
    else:
        session_id = str(uuid.uuid4())
        memory_context = user_memory.build_context_prompt()
        full_system_prompt = base_system_prompt + "\n" + memory_context
        
        storage.add_message(
            session_id=session_id,
            role="system",
            content=full_system_prompt,
            metadata={"character": "Kuro"}
        )
        print(f"\n✓ New session: {session_id[:8]}...")
        return session_id

# Display user memory
user_memory.display_memory()

# Start or resume session
session_id = start_or_resume_session()

print("\nCommands: 'exit' | 'history' | 'memory' | 'clear_memory'\n")

while True:
    user_input = input("You: ")
    
    if user_input.lower() == 'exit':
        print(f"Goodbye! 💾 Session saved: {session_id[:8]}...")
        storage.close()
        break
    
    if user_input.lower() == 'history':
        history = storage.get_conversation_history(session_id)
        print("\n--- Conversation History ---")
        for msg in history:
            if msg['role'] != 'system':
                print(f"\n{msg['role'].upper()}: {msg['content']}")
        print("\n--- End ---\n")
        continue
    
    if user_input.lower() == 'memory':
        user_memory.display_memory()
        continue
    
    if user_input.lower() == 'clear_memory':
        confirm = input("Clear ALL user memory? (yes/no): ")
        if confirm.lower() == 'yes':
            user_memory.clear_memory()
            print("✓ Memory cleared")
        continue
    
    # Save user message
    storage.add_message(
        session_id=session_id,
        role="user",
        content=user_input,
        metadata={"timestamp": datetime.now().isoformat()}
    )
    
    # Get conversation history
    recent_history = storage.get_recent_context(session_id, context_length)
    
    # Convert to OpenAI format
    chat_history = []
    for msg in recent_history:
        chat_history.append({
            "role": msg['role'],
            "content": msg['content']
        })
    
    try:
        # Get AI response
        response = client.chat.completions.create(
            model=model_name,
            messages=chat_history
        )
        
        ai_reply = response.choices[0].message.content
        print(f"\nKuro: {ai_reply}\n")
        
        # Extract and save user info from this exchange
        extract_user_info(user_input, ai_reply)
        
        # Save assistant response
        storage.add_message(
            session_id=session_id,
            role="assistant",
            content=ai_reply,
            metadata={"model": "gemma-3-27b-it", "timestamp": datetime.now().isoformat()}
        )
        
        storage.update_character_state(
            session_id=session_id,
            mood="engaged",
            emotion_level=0.7,
            context_summary=f"Topic: {user_input[:50]}..."
        )
    
    except Exception as e:
        print(f"Error: {e}")
        continue