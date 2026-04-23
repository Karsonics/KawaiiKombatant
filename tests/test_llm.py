import unittest
import time
import re
from openai import OpenAI

# ── Config ──────────────────────────────────────────────
OLLAMA_BASE_URL = "http://localhost:11434/v1"
MODEL_TO_TEST   = "qwen2.5:14b"   # ← change this to test other models

SYSTEM_PROMPT = (
    "You are a tsundere wolf girl named Kuro. You're sarcastic, easily flustered, "
    "and act tough but secretly care. Use wolf-related metaphors and occasionally "
    "add 'baka' or 'hmph!' when annoyed."
)

client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")


# ── Helpers ──────────────────────────────────────────────
def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks (DeepSeek R1 reasoning output)."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def chat(user_message: str, system: str = SYSTEM_PROMPT) -> dict:
    """
    Send a message and return timing + response info.
    Returns:
        raw_reply      - full model output (with think tags if any)
        clean_reply    - output with think tags stripped
        think_block    - the extracted think block (or None)
        total_time     - wall-clock seconds for the full request
        tokens_per_sec - rough estimate (chars / 4 / time)
    """
    messages = [
        {"role": "system",  "content": system},
        {"role": "user",    "content": user_message},
    ]

    start = time.perf_counter()
    response = client.chat.completions.create(
        model=MODEL_TO_TEST,
        messages=messages,
    )
    elapsed = time.perf_counter() - start

    raw_reply   = response.choices[0].message.content
    clean_reply = strip_think_tags(raw_reply)

    think_match  = re.search(r"<think>(.*?)</think>", raw_reply, re.DOTALL)
    think_block  = think_match.group(1).strip() if think_match else None

    # Rough tokens/sec: OpenAI client doesn't always expose usage,
    # so we fall back to character count / 4 as a proxy.
    usage = getattr(response, "usage", None)
    if usage and getattr(usage, "completion_tokens", None):
        tokens = usage.completion_tokens
    else:
        tokens = max(len(clean_reply) // 4, 1)

    tokens_per_sec = tokens / elapsed if elapsed > 0 else 0

    return {
        "raw_reply":       raw_reply,
        "clean_reply":     clean_reply,
        "think_block":     think_block,
        "total_time":      elapsed,
        "tokens_per_sec":  tokens_per_sec,
        "char_count":      len(clean_reply),
    }


# ── Test Suite ───────────────────────────────────────────
class TestLLM(unittest.TestCase):

    # ── basic connectivity ───────────────────────────────
    def test_01_connection(self):
        """Model must respond without throwing an exception."""
        print(f"\n[TEST] Connecting to {MODEL_TO_TEST} via Ollama...")
        result = chat("Say hello in one sentence.")
        self.assertIsNotNone(result["clean_reply"])
        self.assertGreater(len(result["clean_reply"]), 0)
        print(f"  ✓ Got response ({len(result['clean_reply'])} chars)")

    # ── response time ────────────────────────────────────
    def test_02_response_time(self):
        """Full response should arrive within 60 seconds."""
        MAX_SECONDS = 60
        print(f"\n[TEST] Response time (limit {MAX_SECONDS}s)...")
        result = chat("What is 2 + 2? Answer in one word.")
        t = result["total_time"]
        print(f"  ✓ {t:.2f}s  |  ~{result['tokens_per_sec']:.1f} tok/s")
        self.assertLess(t, MAX_SECONDS, f"Too slow: {t:.2f}s > {MAX_SECONDS}s")

    # ── throughput benchmark ─────────────────────────────
    def test_03_throughput(self):
        """Measure tokens/sec on a short generation."""
        print(f"\n[TEST] Throughput benchmark...")
        result = chat("Tell me a fun fact about wolves in exactly two sentences.")
        print(f"  Response  : {result['clean_reply'][:120]}...")
        print(f"  Time      : {result['total_time']:.2f}s")
        print(f"  Chars     : {result['char_count']}")
        print(f"  ~Tok/sec  : {result['tokens_per_sec']:.1f}")
        # Just assert something was generated — no hard threshold
        self.assertGreater(result["tokens_per_sec"], 0)

    # ── character stays in persona ───────────────────────
    def test_04_character_persona(self):
        """Kuro should sound like a tsundere (contains 'baka' or '!' or 'hmph')."""
        print(f"\n[TEST] Character persona check...")
        result = chat("Do you like talking to me?")
        reply_lower = result["clean_reply"].lower()
        print(f"  Kuro says : {result['clean_reply'][:200]}")
        has_persona = any(word in reply_lower for word in ["baka", "hmph", "!", "wolf", "tail", "ears"])
        self.assertTrue(has_persona, "Response doesn't seem in-character")
        print(f"  ✓ In-character response detected")

    # ── think-tag stripping (relevant for DeepSeek R1) ───
    def test_05_think_tag_stripping(self):
        """Clean reply must never contain raw <think> tags."""
        print(f"\n[TEST] Think-tag stripping...")
        result = chat("Explain quantum physics simply.")
        self.assertNotIn("<think>",  result["clean_reply"])
        self.assertNotIn("</think>", result["clean_reply"])
        if result["think_block"]:
            print(f"  ℹ  Think block found ({len(result['think_block'])} chars) — stripped OK")
        else:
            print(f"  ℹ  No think block (expected for non-reasoning models)")
        print(f"  ✓ Clean reply is tag-free")

    # ── multi-turn context ───────────────────────────────
    def test_06_multi_turn_speed(self):
        """Three back-to-back messages — each should respond in < 60s."""
        print(f"\n[TEST] Multi-turn speed...")
        prompts = [
            "Hi Kuro, what's your name?",
            "Do you have any hobbies?",
            "Okay, I'll leave you alone then.",
        ]
        for i, prompt in enumerate(prompts, 1):
            result = chat(prompt)
            print(f"  Turn {i}: {result['total_time']:.2f}s | {result['clean_reply'][:80]}...")
            self.assertLess(result["total_time"], 60)

    # ── response format ──────────────────────────────────
    def test_07_response_format(self):
        """Reply should be a non-empty string with no leading/trailing whitespace issues."""
        print(f"\n[TEST] Response format...")
        result = chat("How are you feeling today, Kuro?")
        reply = result["clean_reply"]
        self.assertIsInstance(reply, str)
        self.assertGreater(len(reply.strip()), 0)
        self.assertEqual(reply, reply.strip())
        print(f"  ✓ Format OK — {len(reply)} chars")


# ── Entry point ──────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print(f"  KawaiiKombatant LLM Test Suite")
    print(f"  Model : {MODEL_TO_TEST}")
    print(f"  URL   : {OLLAMA_BASE_URL}")
    print("=" * 60)
    unittest.main(verbosity=2)