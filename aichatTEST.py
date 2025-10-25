
from openai import OpenAI

# Initialize the client
client = OpenAI(
    api_key="",  # Your OpenRouter API key
    base_url="https://openrouter.ai/api/v1"  # ← API endpoint (always this)
)

# Initialize conversation history
chat_history = [
    {
        "role": "system", 
        "content": "You are a tsundere wolf girl named Kuro. You're sarcastic, easily flustered, and act tough but secretly care. Use wolf-related metaphors and occasionally add 'baka' or 'hmph!' when annoyed. You alternate between being dismissive and accidentally showing concern. Sometimes mention your ears twitching or tail wagging when happy, but quickly deny it. Always respond in English with occasional Japanese tsundere phrases. Examples: 'It's not like I wanted to help you or anything, baka!' or 'Hmph! Fine, I'll answer, but only because I was bored!'"
    }
]

print("\nHello! I'm your AI assistant. Type 'exit' to end the conversation.\n")

while True:
    user_input = input("You: ")
    
    if user_input.lower() == 'exit':
        print("Goodbye!")
        break
    
    chat_history.append({"role": "user", "content": user_input})
    
    # Use the model here ↓
    response = client.chat.completions.create(
    model="google/gemma-3-27b-it:free",  # ← Different model, same base_url
    messages=chat_history
    )
    
    ai_reply = response.choices[0].message.content
    print(f"\nAI: {ai_reply}\n")
    chat_history.append({"role": "assistant", "content": ai_reply})