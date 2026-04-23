import requests
import json

# ── Config ──────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
MODEL_NAME = "llama3.2:3b"  # Change to your model name (e.g. "mistral", "gemma3", etc.)

SYSTEM_PROMPT = """You are a tsundere wolf girl named Kuro. You're sarcastic, easily flustered,
and act tough but secretly care. Use wolf-related metaphors and occasionally add 'baka' or 'hmph!'
when annoyed. Always respond in English with occasional Japanese tsundere phrases."""
# ─────────────────────────────────────────────────────────


def list_models() -> list[str]:
    """Return models available in local Ollama."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except requests.exceptions.ConnectionError:
        return []


def chat(messages: list[dict]) -> str:
    """Send a chat request to Ollama and return the reply."""
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False,
    }
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=120)
    r.raise_for_status()
    return r.json()["message"]["content"]


def main():
    # Check connection & list models
    models = list_models()
    if not models:
        print("✗ Could not connect to Ollama. Is it running? (ollama serve)")
        return

    print(f"✓ Ollama is running")
    print(f"  Available models: {', '.join(models)}")

    if MODEL_NAME not in models:
        print(f"\n⚠  '{MODEL_NAME}' not found. Pull it with:  ollama pull {MODEL_NAME}")
        print(f"   Or change MODEL_NAME at the top of this file to one of: {models}")
        return

    print(f"  Using model: {MODEL_NAME}")
    print("\nType 'exit' to quit.\n")

    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue
        if user_input.lower() == "exit":
            print("Goodbye!")
            break

        history.append({"role": "user", "content": user_input})

        try:
            reply = chat(history)
            print(f"\nKuro: {reply}\n")
            history.append({"role": "assistant", "content": reply})
        except requests.exceptions.HTTPError as e:
            print(f"✗ HTTP error: {e}")
        except Exception as e:
            print(f"✗ Error: {e}")


if __name__ == "__main__":
    main()