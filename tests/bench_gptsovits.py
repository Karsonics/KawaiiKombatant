"""
GPT-SoVITS Speed Benchmark  (api_v2.py edition)
=================================================
Hits your already-running api_v2.py server on port 9880.

Start your server first (in another terminal):
    conda activate GPTSoVits
    cd /home/weeb_user/GPT-SoVITS
    PYTORCH_ROCM_ARCH=gfx1200 PYTHONPATH=/home/weeb_user/GPT-SoVITS python api_v2.py -a 0.0.0.0 -p 9880

Then run this benchmark:
    python bench_gptsovits.py \
        --ref_audio /absolute/path/to/reference.wav \
        --ref_text  "Exactly what the reference audio says." \
        --ref_lang  en

Note: ref_audio must be an absolute path that the SERVER can read from disk.
"""

import argparse
import time
import sys
import wave
import io
import statistics

parser = argparse.ArgumentParser(description="GPT-SoVITS api_v2 speed benchmark")
parser.add_argument("--ref_audio", required=True,
                    help="Absolute path to reference WAV (readable by the server process)")
parser.add_argument("--ref_text",  required=True,
                    help="Transcript of what is said in the reference audio")
parser.add_argument("--ref_lang",  default="en",
                    help="Language of the reference audio: en / zh / ja  (default: en)")
parser.add_argument("--text_lang", default=None,
                    help="Language of the text to synthesise — defaults to --ref_lang")
parser.add_argument("--host",      default="127.0.0.1")
parser.add_argument("--port",      default=9880, type=int)
parser.add_argument("--runs",      default=3, type=int,
                    help="Number of timed runs per sentence (default: 3)")
args = parser.parse_args()

if args.text_lang is None:
    args.text_lang = args.ref_lang

try:
    import requests
except ImportError:
    print("[ERROR] 'requests' not installed.  Run:  pip install requests")
    sys.exit(1)

BASE = f"http://{args.host}:{args.port}"

# ── Header ────────────────────────────────────────────────────────────────────
print("\n" + "="*62)
print("  GPT-SoVITS Speed Benchmark  (api_v2)")
print("="*62)
print(f"  Server     : {BASE}")
print(f"  Ref audio  : {args.ref_audio}")
print(f"  Ref lang   : {args.ref_lang}   Text lang: {args.text_lang}")
print(f"  Runs/sentence : {args.runs}")

# ── Check server ──────────────────────────────────────────────────────────────
try:
    # A quick probe — will probably get a 400/422 (missing params) but that proves the server is up
    requests.get(f"{BASE}/tts", timeout=4)
    print(f"  Status     : Server reachable ✓")
except requests.exceptions.ConnectionError:
    print(
        f"\n[ERROR] Cannot connect to GPT-SoVITS at {BASE}\n"
        "Make sure api_v2.py is running first.\n"
    )
    sys.exit(1)
except Exception:
    print(f"  Status     : Server reachable ✓")

print("="*62 + "\n")

# ── Sentences to test ─────────────────────────────────────────────────────────
SENTENCES = [
    ("short",  "Hello, how are you doing today?"),
    ("medium", "The quick brown fox jumps over the lazy dog near the riverbank at sunset."),
    ("long",   (
        "Artificial intelligence has transformed many industries over the past decade, "
        "enabling machines to perform tasks that previously required human intelligence, "
        "from recognizing speech and images to translating languages and driving cars."
    )),
]

# ── Helpers ───────────────────────────────────────────────────────────────────
def wav_duration(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as f:
            return f.getnframes() / float(f.getframerate())
    except Exception:
        return 0.0

def synthesise(text: str) -> tuple:
    """Call /tts and return (wav_bytes, elapsed_sec)."""
    payload = {
        "text":              text,
        "text_lang":         args.text_lang,
        "ref_audio_path":    args.ref_audio,
        "prompt_text":       args.ref_text,
        "prompt_lang":       args.ref_lang,
        "media_type":        "wav",
        "streaming_mode":    False,
        "batch_size":        1,
        "text_split_method": "cut5",
    }
    t0 = time.perf_counter()
    r = requests.post(f"{BASE}/tts", json=payload, timeout=300)
    elapsed = time.perf_counter() - t0

    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} — {r.text[:150]}")
    return r.content, elapsed

# ── Warm-up (first call initialises VRAM, not fair to count it) ───────────────
print("  Warming up — first inference loads weights into VRAM...")
t0 = time.time()
try:
    synthesise("Warming up the model.")
    print(f"  Warm-up done in {time.time()-t0:.1f}s  (not included in results)\n")
except Exception as e:
    print(f"\n[ERROR] Warm-up failed: {e}")
    print("  → Check that the server is running and --ref_audio path is correct.\n")
    sys.exit(1)

# ── Timed runs ────────────────────────────────────────────────────────────────
results = []

for label, text in SENTENCES:
    print(f"  Benchmarking [{label}]  ({len(text)} chars,  {args.runs} runs)")
    times = []
    audio_sec = 0.0

    for i in range(args.runs):
        try:
            data, elapsed = synthesise(text)
            times.append(elapsed)
            if i == 0:
                audio_sec = wav_duration(data)
            print(f"    run {i+1}: {elapsed:.2f}s")
        except Exception as e:
            print(f"    run {i+1}: FAILED — {e}")

    good = [t for t in times if t == t]
    if not good:
        print("    → all runs failed, skipping\n")
        continue

    avg  = statistics.mean(good)
    best = min(good)
    rtf  = avg / audio_sec if audio_sec > 0 else float("nan")
    results.append(dict(label=label, chars=len(text),
                        audio_sec=audio_sec, avg=avg, best=best, rtf=rtf))
    print(f"    → avg {avg:.2f}s | best {best:.2f}s | "
          f"audio {audio_sec:.2f}s | RTF {rtf:.2f}x\n")

# ── Summary ───────────────────────────────────────────────────────────────────
print("="*62)
print("  RESULTS — GPT-SoVITS  (port 9880)")
print("="*62)
print(f"  {'Sentence':<8} {'Chars':>5} {'Audio':>7} {'Avg':>7} {'Best':>7} {'RTF':>7}")
print(f"  {'-'*8} {'-'*5} {'-'*7} {'-'*7} {'-'*7} {'-'*7}")
for r in results:
    print(f"  {r['label']:<8} {r['chars']:>5} "
          f"{r['audio_sec']:>6.2f}s "
          f"{r['avg']:>6.2f}s "
          f"{r['best']:>6.2f}s "
          f"{r['rtf']:>6.2f}x")
print("="*62)
print("  RTF < 1.0 = faster than real-time  |  RTF > 1.0 = slower")
print("="*62 + "\n")