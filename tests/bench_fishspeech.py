"""
Fish Speech Speed Benchmark  (openaudio-s1-mini)
=================================================
Hits your already-running api_server.py on port 8082.

Start your server first (in another terminal):
    conda activate FishSpeech
    cd /mnt/hdd500/fish-speech-rocm
    PYTORCH_ROCM_ARCH=gfx1200 python tools/api_server.py --listen 0.0.0.0:8082 \
        --llama-checkpoint-path checkpoints/openaudio-s1-mini \
        --decoder-checkpoint-path checkpoints/openaudio-s1-mini/codec.pth

Then run this benchmark:
    python bench_fishspeech.py

Optionally pass a reference audio for voice cloning:
    python bench_fishspeech.py --ref_audio /path/to/voice.wav --ref_text "What it says."

Without --ref_audio the model uses its random base voice.
"""

import argparse
import time
import sys
import os
import wave
import io
import statistics

parser = argparse.ArgumentParser(description="Fish Speech speed benchmark")
parser.add_argument("--ref_audio", default=None,
                    help="Optional path to reference WAV for voice cloning")
parser.add_argument("--ref_text",  default=None,
                    help="Transcript of the reference audio (improves cloning quality)")
parser.add_argument("--host",      default="127.0.0.1")
parser.add_argument("--port",      default=8082, type=int)
parser.add_argument("--runs",      default=3, type=int,
                    help="Number of timed runs per sentence (default: 3)")
args = parser.parse_args()

try:
    import requests
except ImportError:
    print("[ERROR] 'requests' not installed.  Run:  pip install requests")
    sys.exit(1)

BASE = f"http://{args.host}:{args.port}"

# ── Header ────────────────────────────────────────────────────────────────────
print("\n" + "="*62)
print("  Fish Speech Speed Benchmark  (openaudio-s1-mini)")
print("="*62)
print(f"  Server     : {BASE}")
if args.ref_audio:
    if not os.path.exists(args.ref_audio):
        print(f"\n[ERROR] Reference audio not found: {args.ref_audio}")
        sys.exit(1)
    print(f"  Voice      : {args.ref_audio}  (cloning enabled)")
else:
    print(f"  Voice      : Base model  (no reference audio)")
print(f"  Runs/sentence : {args.runs}")

# ── Check server ──────────────────────────────────────────────────────────────
try:
    requests.get(f"{BASE}/v1/models", timeout=5)
    print(f"  Status     : Server reachable ✓")
except requests.exceptions.ConnectionError:
    print(
        f"\n[ERROR] Cannot connect to Fish Speech at {BASE}\n"
        "Make sure api_server.py is running first.\n"
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
    """POST to /v1/tts and return (wav_bytes, elapsed_sec)."""
    data   = {"text": text, "format": "wav", "streaming": "false"}
    files  = {}

    if args.ref_text:
        data["reference_text"] = args.ref_text

    if args.ref_audio:
        files["reference_audio"] = (
            os.path.basename(args.ref_audio),
            open(args.ref_audio, "rb"),
            "audio/wav",
        )

    t0 = time.perf_counter()
    r = requests.post(
        f"{BASE}/v1/tts",
        files=files if files else None,
        data=data,
        timeout=300,
    )
    elapsed = time.perf_counter() - t0

    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} — {r.text[:150]}")
    return r.content, elapsed

# ── Warm-up ───────────────────────────────────────────────────────────────────
print("  Warming up — first inference loads weights into VRAM...")
t0 = time.time()
try:
    synthesise("Warming up the model.")
    print(f"  Warm-up done in {time.time()-t0:.1f}s  (not included in results)\n")
except Exception as e:
    print(f"\n[ERROR] Warm-up failed: {e}")
    print("  → Check that the server started successfully and try again.\n")
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
print("  RESULTS — Fish Speech  (port 8082,  openaudio-s1-mini)")
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