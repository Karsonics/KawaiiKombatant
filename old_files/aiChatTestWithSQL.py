from openai import OpenAI
from server.process.storage.mysql_storage import MySQLConversationStorage
import uuid
from datetime import datetime

# Initialize the OpenRouter client
client = OpenAI(
    api_key="",  # Your OpenRouter API key
    base_url="https://openrouter.ai/api/v1"
)

# Initialize MySQL storage
storage = MySQLConversationStorage("configs/database_config.yaml")

# Generate or load session ID
session_id = str(uuid.uuid4())  # Creates unique session ID
print(f"Session ID: {session_id}")

# System prompt for the character
system_prompt = """You are a tsundere wolf girl named Kuro. You're sarcastic, easily flustered, and act tough but secretly care. Use wolf-related metaphors and occasionally add 'baka' or 'hmph!' when annoyed. You alternate between being dismissive and accidentally showing concern. Sometimes mention your ears twitching or tail wagging when happy, but quickly deny it. Always respond in English with occasional Japanese tsundere phrases. Examples: 'It's not like I wanted to help you or anything, baka!' or 'Hmph! Fine, I'll answer, but only because I was bored!'"""

# Add system message to database
storage.add_message(
    session_id=session_id,
    role="system",
    content=system_prompt,
    metadata={"character": "Kuro", "personality": "tsundere"}
)

# Initialize character state
storage.update_character_state(
    session_id=session_id,
    mood="neutral",
    emotion_level=0.5,
    context_summary="Starting new conversation"
)

print("\nHello! I'm your AI assistant. Type 'exit' to end the conversation.")
print("Type 'history' to see conversation history.\n")

while True:
    user_input = input("You: ")
    
    if user_input.lower() == 'exit':
        print("Goodbye! Conversation saved to database.")
        storage.close()
        break
    
    if user_input.lower() == 'history':
        # Display conversation history from database
        history = storage.get_conversation_history(session_id)
        print("\n--- Conversation History ---")
        for msg in history:
            if msg['role'] != 'system':
                print(f"{msg['role'].upper()}: {msg['content'][:100]}...")
        print("--- End History ---\n")
        continue
    
    # Save user message to database
    storage.add_message(
        session_id=session_id,
        role="user",
        content=user_input,
        metadata={"timestamp": datetime.now().isoformat()}
    )
    
    # Get recent conversation history from database
    recent_history = storage.get_recent_context(session_id, context_length=10)
    
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
            model="google/gemma-3-27b-it:free",
            messages=chat_history
        )
        
        ai_reply = response.choices[0].message.content
        print(f"\nAI: {ai_reply}\n")
        
        # Save assistant response to database
        storage.add_message(
            session_id=session_id,
            role="assistant",
            content=ai_reply,
            metadata={"model": "gemma-3-27b-it", "timestamp": datetime.now().isoformat()}
        )
        
        # Update character state (optional - you can make this more sophisticated)
        current_state = storage.get_character_state(session_id)
        if current_state:
            # Simple mood tracking based on message count
            storage.update_character_state(
                session_id=session_id,
                mood="engaged",
                emotion_level=0.7,
                context_summary=f"Last message: {user_input[:50]}..."
            )
    
    except Exception as e:
        print(f"Error: {e}")
        continue