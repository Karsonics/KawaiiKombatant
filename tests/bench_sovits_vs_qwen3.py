#!/usr/bin/env python3
"""
GPT-SoVITS v1/v2 vs Qwen3-TTS  —  Comparative Speed Benchmark
================================================================

Tests four TTS configurations against identical sentences using the same
reference audio, then produces a side-by-side comparison.

Usage:
    python bench_sovits_vs_qwen3.py \
        --ref_audio /path/to/reference.wav \
        --ref_text  "What the reference says" \
        --output "./benchmark_results/gpt so vits vs qwen 3" \
        --runs 5

The script walks you through starting/stopping servers sequentially.
GPT-SoVITS v1 and v2 share port 9880; Qwen3 runs on port 8880.
"""

import argparse
import base64
import csv
import io
import json
import os
import statistics
import sys
import time
import wave
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

try:
    import requests
except ImportError:
    print("[ERROR] 'requests' not installed. Run: pip install requests")
    sys.exit(1)

# ── constants ──────────────────────────────────────────────────────────────────
_WARMUP_TEXT = "Warming up the model."
_SERVER_PROBE_TIMEOUT = 8


# ── data structures ────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    label: str
    category: str
    text: str
    chars: int
    words: int
    audio_duration: float = 0.0
    times: list = field(default_factory=list)
    wav_bytes: int = 0
    failures: int = 0
    error_messages: list = field(default_factory=list)

    @property
    def successful_runs(self) -> int:
        return len(self.times)

    @property
    def avg_time(self) -> float:
        if not self.times:
            return 0.0
        return statistics.mean(self.times)

    @property
    def median_time(self) -> float:
        if not self.times:
            return 0.0
        return statistics.median(self.times)

    @property
    def min_time(self) -> float:
        if not self.times:
            return 0.0
        return min(self.times)

    @property
    def max_time(self) -> float:
        if not self.times:
            return 0.0
        return max(self.times)

    @property
    def p95(self) -> float:
        if not self.times:
            return 0.0
        s = sorted(self.times)
        return s[int(len(s) * 0.95)] if len(s) > 1 else s[0]

    @property
    def p99(self) -> float:
        if not self.times:
            return 0.0
        s = sorted(self.times)
        return s[int(len(s) * 0.99)] if len(s) > 1 else s[0]

    @property
    def stddev(self) -> float:
        if len(self.times) < 2:
            return 0.0
        return statistics.stdev(self.times)

    @property
    def rtf(self) -> float:
        if self.audio_duration <= 0:
            return float("nan")
        return self.avg_time / self.audio_duration

    @property
    def chars_per_sec(self) -> float:
        if self.avg_time <= 0:
            return 0.0
        return self.chars / self.avg_time


# ── sentence catalog ───────────────────────────────────────────────────────────

SENTENCE_CATALOG = {
    "short": [
        "Hello!",
        "How are you?",
        "Nice to meet you.",
        "Good morning!",
        "Thank you.",
    ],
    "medium": [
        "The quick brown fox jumps over the lazy dog.",
        "Artificial intelligence is transforming our world.",
        "Welcome to the future of voice synthesis.",
        "Machine learning enables incredible capabilities.",
        "Speech synthesis has come a long way.",
    ],
    "long": [
        (
            "Artificial intelligence has transformed many industries over the past decade, "
            "enabling machines to perform tasks that previously required human intelligence, "
            "from recognizing speech and images to translating languages and driving cars."
        ),
        (
            "The history of computing spans thousands of years, from the abacus in ancient "
            "times to modern quantum computers, each generation building upon the innovations "
            "of those who came before."
        ),
        (
            "Climate change represents one of the greatest challenges facing humanity in the "
            "21st century, requiring coordinated global action to mitigate its effects and "
            "adapt to its consequences."
        ),
    ],
    "very_long": [
        (
            "In the realm of artificial intelligence and machine learning, researchers have "
            "made remarkable strides in developing systems capable of understanding, processing, "
            "and generating human language. These advances have applications ranging from virtual "
            "assistants and chatbots to automated translation services and content generation. "
            "The ability of machines to not only comprehend but also produce natural-sounding "
            "speech represents a particularly fascinating frontier in AI development, one that "
            "combines expertise from acoustics, linguistics, and deep learning."
        ),
        (
            "Throughout history, human beings have sought to preserve and transmit knowledge "
            "across generations and cultures. From the earliest cave paintings and oral traditions "
            "to the invention of writing, the printing press, and now digital technologies, each "
            "new medium for recording and sharing information has fundamentally transformed society. "
            "Today, we stand at another pivotal moment in this ongoing story, where the boundaries "
            "between human and machine creativity are becoming increasingly blurred."
        ),
    ],
    "stress": [
        "Supercalifragilisticexpialidocious!",
        (
            "The five boxing wizards jump quickly. A quick movement of the enemy will jeopardize "
            "six gunboats. All questions asked by five expert boxing wizards are answered!"
        ),
        (
            "Unique New York Unique New York Unique New York You know you need unique New York "
            "Unique New York Unique New York"
        ),
    ],
    "punctuation": [
        "Hello, world! How's it going? I'm doing great... really, I am!",
        "Wait... did you hear that? Yes, yes, I did! Absolutely incredible!",
        "Call me at 1-800-555-0199. Or email: test@example.com. Maybe both?",
    ],
    "numbers": [
        "The population of Earth is approximately 8 billion people as of 2024.",
        "Pi is approximately 3.14159. The square root of 2 is about 1.41421.",
        "In 1984, Apple released the Macintosh. In 2007, they introduced the iPhone.",
    ],
    "kuro": [
        "Hmph! Baka. What do you want?",
        "It's not like I like you or anything... Don't get the wrong idea!",
        "My tail is NOT wagging! I'm just... happy about something else entirely.",
    ],
}


# ── helpers ────────────────────────────────────────────────────────────────────

def wav_duration(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as f:
            return f.getnframes() / float(f.getframerate())
    except Exception:
        return 0.0


def verify_audio(data: bytes) -> dict:
    result = {"valid": False, "duration": 0.0, "sample_rate": 0, "channels": 0}
    try:
        with wave.open(io.BytesIO(data), "rb") as f:
            result["valid"] = True
            result["duration"] = f.getnframes() / float(f.getframerate())
            result["sample_rate"] = f.getframerate()
            result["channels"] = f.getnchannels()
    except Exception:
        pass
    return result


def ensure_dir(path: str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def print_header(title: str, width: int = 80) -> None:
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def pretty_rtf(rtf: float) -> str:
    if rtf <= 0:
        return "  N/A"
    if rtf < 1.0:
        return f"{rtf:.2f}x ✓"
    elif rtf < 2.0:
        return f"{rtf:.2f}x ~"
    else:
        return f"{rtf:.2f}x ✗"


def wait_for_server(base_url: str, timeout: int = _SERVER_PROBE_TIMEOUT) -> bool:
    try:
        requests.get(base_url, timeout=timeout)
        return True
    except requests.exceptions.ConnectionError:
        return False
    except Exception:
        return True


def prompt_enter(msg: str) -> None:
    print(f"\n  {msg}")
    input("  Press Enter to continue...")


# ── backend abstractions ───────────────────────────────────────────────────────

class TTSBackend(ABC):
    """Abstract TTS backend for benchmarking."""

    def __init__(self, args):
        self.args = args
        self.total_requests = 0
        self.failed_requests = 0

    @abstractmethod
    def check_server(self) -> bool:
        ...

    @abstractmethod
    def warmup(self) -> Tuple[float, float]:
        ...

    @abstractmethod
    def synthesize(self, text: str) -> Tuple[bytes, float]:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def tag(self) -> str:
        ...


class GPTSovitsBackend(TTSBackend):
    """Base for GPT-SoVITS v1 and v2 (same /tts POST API)."""

    def __init__(self, args, tag: str, name: str):
        super().__init__(args)
        self._tag = tag
        self._name = name
        self.host = args.gptsovits_host
        self.port = args.gptsovits_port
        self.base = f"http://{self.host}:{self.port}"
        self.ref_audio = args.ref_audio
        self.ref_text = args.ref_text
        self.ref_lang = args.ref_lang
        self.text_lang = args.text_lang or args.ref_lang

    @property
    def name(self) -> str:
        return self._name

    @property
    def tag(self) -> str:
        return self._tag

    def check_server(self) -> bool:
        try:
            requests.get(f"{self.base}/tts", timeout=_SERVER_PROBE_TIMEOUT)
            return True
        except requests.exceptions.ConnectionError:
            return False
        except Exception:
            return True

    def warmup(self) -> Tuple[float, float]:
        text = _WARMUP_TEXT
        t0 = time.perf_counter()
        data, _ = self.synthesize(text)
        elapsed = time.perf_counter() - t0
        audio_info = verify_audio(data)
        return elapsed, audio_info["duration"]

    def synthesize(self, text: str) -> Tuple[bytes, float]:
        payload = {
            "text": text,
            "text_lang": self.text_lang,
            "ref_audio_path": self.ref_audio,
            "prompt_text": self.ref_text,
            "prompt_lang": self.ref_lang,
            "media_type": "wav",
            "streaming_mode": False,
            "batch_size": 1,
            "text_split_method": "cut5",
        }
        t0 = time.perf_counter()
        r = requests.post(f"{self.base}/tts", json=payload, timeout=300)
        elapsed = time.perf_counter() - t0
        self.total_requests += 1
        if r.status_code != 200:
            self.failed_requests += 1
            raise RuntimeError(f"HTTP {r.status_code} — {r.text[:150]}")
        return r.content, elapsed


class GPTSovitsV1Backend(GPTSovitsBackend):
    def __init__(self, args):
        super().__init__(args, tag="gpt_sovits_v1", name="GPT-SoVITS v1")


class GPTSovitsV2Backend(GPTSovitsBackend):
    def __init__(self, args):
        super().__init__(args, tag="gpt_sovits_v2", name="GPT-SoVITS v2 (cached)")


class Qwen3PresetBackend(TTSBackend):
    """Qwen3 CustomVoice with preset speaker (faster, no voice cloning)."""

    def __init__(self, args):
        super().__init__(args)
        self.host = args.qwen3_host
        self.port = args.qwen3_port
        self.base = f"http://{self.host}:{self.port}"

    @property
    def name(self) -> str:
        return "Qwen3-TTS Preset"

    @property
    def tag(self) -> str:
        return "qwen3_preset"

    def check_server(self) -> bool:
        try:
            r = requests.get(f"{self.base}/health", timeout=_SERVER_PROBE_TIMEOUT)
            return r.status_code == 200
        except Exception:
            return False

    def warmup(self) -> Tuple[float, float]:
        t0 = time.perf_counter()
        data, _ = self.synthesize(_WARMUP_TEXT)
        elapsed = time.perf_counter() - t0
        audio_info = verify_audio(data)
        return elapsed, audio_info["duration"]

    def synthesize(self, text: str) -> Tuple[bytes, float]:
        payload = {
            "model": "tts-1",
            "input": text,
            "voice": "nova",
            "response_format": "wav",
        }
        t0 = time.perf_counter()
        r = requests.post(
            f"{self.base}/v1/audio/speech", json=payload, timeout=300
        )
        elapsed = time.perf_counter() - t0
        self.total_requests += 1
        if r.status_code != 200:
            self.failed_requests += 1
            raise RuntimeError(f"HTTP {r.status_code} — {r.text[:150]}")
        return r.content, elapsed


class Qwen3CloneBackend(TTSBackend):
    """Qwen3 voice cloning (same reference audio as GPT-SoVITS)."""

    def __init__(self, args):
        super().__init__(args)
        self.host = args.qwen3_host
        self.port = args.qwen3_port
        self.base = f"http://{self.host}:{self.port}"
        self.ref_text = args.ref_text
        # Pre-encode reference audio once
        with open(args.ref_audio, "rb") as f:
            self._ref_audio_b64 = base64.b64encode(f.read()).decode()

    @property
    def name(self) -> str:
        return "Qwen3-TTS Clone"

    @property
    def tag(self) -> str:
        return "qwen3_clone"

    def check_server(self) -> bool:
        try:
            r = requests.get(f"{self.base}/health", timeout=_SERVER_PROBE_TIMEOUT)
            return r.status_code == 200
        except Exception:
            return False

    def warmup(self) -> Tuple[float, float]:
        t0 = time.perf_counter()
        data, _ = self.synthesize(_WARMUP_TEXT)
        elapsed = time.perf_counter() - t0
        audio_info = verify_audio(data)
        return elapsed, audio_info["duration"]

    def synthesize(self, text: str) -> Tuple[bytes, float]:
        payload = {
            "model": "tts-1",
            "input": text,
            "ref_audio": self._ref_audio_b64,
            "ref_text": self.ref_text,
            "language": "english",
            "response_format": "wav",
        }
        t0 = time.perf_counter()
        r = requests.post(
            f"{self.base}/v1/audio/voice-clone", json=payload, timeout=300
        )
        elapsed = time.perf_counter() - t0
        self.total_requests += 1
        if r.status_code != 200:
            self.failed_requests += 1
            raise RuntimeError(f"HTTP {r.status_code} — {r.text[:150]}")
        return r.content, elapsed


# ── benchmark runner ───────────────────────────────────────────────────────────

class ComparativeBenchmark:
    def __init__(self, args):
        self.args = args
        self.output_dir = ensure_dir(args.output)
        self.run_count = args.runs
        self.auto_mode = getattr(args, "auto", False)
        self.results: dict[str, list[RunResult]] = {}
        self.backend_meta: dict[str, dict] = {}
        self.backend_meta: dict[str, dict] = {}

    # ── sequential orchestration ───────────────────────────────────────────

    def run(self) -> None:
        print_header("GPT-SoVITS vs Qwen3-TTS — Comparative Benchmark")

        ref_path = Path(self.args.ref_audio)
        print(f"  Reference audio  : {ref_path}")
        print(f"  Reference text   : {self.args.ref_text[:60]}...")
        print(f"  Runs per sentence: {self.run_count}")
        print(f"  Output directory : {self.output_dir}")
        print()

        total_sentences = sum(len(v) for v in SENTENCE_CATALOG.values())

        # ── phase 1: GPT-SoVITS v1 ──────────────────────────────────────
        backend_v1 = GPTSovitsV1Backend(self.args)
        self._run_backend(
            backend_v1,
            total_sentences,
            phase=1,
            total_phases=4,
            start_cmd=(
                "conda activate GPTSoVits\n"
                f"  cd /mnt/storage/GPT-SoVITS\n"
                "  PYTORCH_ROCM_ARCH=gfx1200 PYTHONPATH=/mnt/storage/GPT-SoVITS "
                f"python api_v2.py -a 0.0.0.0 -p {self.args.gptsovits_port}"
            ),
        )

        # ── phase 2: GPT-SoVITS v2 ──────────────────────────────────────
        backend_v2 = GPTSovitsV2Backend(self.args)
        self._run_backend(
            backend_v2,
            total_sentences,
            phase=2,
            total_phases=4,
            start_cmd=(
                "conda activate GPTSoVits\n"
                f"  cd /mnt/storage/gpt-sovits-2\n"
                "  PYTORCH_ROCM_ARCH=gfx1200 PYTHONPATH=/mnt/storage/gpt-sovits-2 "
                f"python api_v2_cache.py -a 0.0.0.0 -p {self.args.gptsovits_port}"
            ),
            stop_msg="Stop the previous GPT-SoVITS server, then start v2.",
        )

        # ── phase 3: Qwen3 preset ──────────────────────────────────────
        backend_qwen_preset = Qwen3PresetBackend(self.args)
        self._run_backend(
            backend_qwen_preset,
            total_sentences,
            phase=3,
            total_phases=4,
            start_cmd=None,  # already running
            stop_msg=None,
        )

        # ── phase 4: Qwen3 clone ───────────────────────────────────────
        backend_qwen_clone = Qwen3CloneBackend(self.args)
        self._run_backend(
            backend_qwen_clone,
            total_sentences,
            phase=4,
            total_phases=4,
            start_cmd=None,
            stop_msg=None,
        )

        # ── final report ───────────────────────────────────────────────
        self._generate_reports()

    def _run_backend(
        self,
        backend: TTSBackend,
        total_sentences: int,
        phase: int,
        total_phases: int,
        start_cmd: Optional[str] = None,
        stop_msg: Optional[str] = None,
    ) -> None:
        print_header(
            f"[Phase {phase}/{total_phases}] {backend.name}  "
            f"(port {self.args.gptsovits_port if 'gpt' in backend.tag else self.args.qwen3_port})",
            70,
        )

        if not self.auto_mode and stop_msg:
            prompt_enter(stop_msg)
        if not self.auto_mode and start_cmd:
            print(f"  Start the server in another terminal:\n  {start_cmd}")

        # Wait for server
        print(f"\n  Waiting for {backend.name} server...", end="", flush=True)
        while True:
            if backend.check_server():
                print(" connected ✓")
                break
            print(".", end="", flush=True)
            time.sleep(2)

        # Warmup
        print(f"  Warming up...", end="", flush=True)
        try:
            warm_elapsed, warm_audio = backend.warmup()
            print(f" {warm_elapsed:.1f}s  (audio: {warm_audio:.2f}s)")
        except Exception as e:
            print(f"\n  [ERROR] Warmup failed: {e}")
            print("  Check that the server is running with the correct reference audio.")
            return

        # Run all sentences
        backend_results: list[RunResult] = []
        total_requests = total_sentences * self.run_count
        completed = 0

        for category, sentences in SENTENCE_CATALOG.items():
            for idx, sentence in enumerate(sentences):
                result = RunResult(
                    label=f"{category}_{idx}",
                    category=category,
                    text=sentence,
                    chars=len(sentence),
                    words=len(sentence.split()),
                )

                for run_i in range(self.run_count):
                    try:
                        data, elapsed = backend.synthesize(sentence)
                        result.times.append(elapsed)
                        if run_i == 0:
                            audio_info = verify_audio(data)
                            result.audio_duration = audio_info["duration"]
                            result.wav_bytes = len(data)
                    except Exception as e:
                        result.failures += 1
                        result.error_messages.append(str(e))

                    completed += 1
                    pct = completed / total_requests * 100
                    bar_len = 30
                    filled = int(bar_len * completed / total_requests)
                    bar = "█" * filled + "░" * (bar_len - filled)
                    print(
                        f"\r  [{bar}] {completed}/{total_requests} ({pct:.0f}%)",
                        end="",
                        flush=True,
                    )

                backend_results.append(result)

        print()  # newline after progress bar

        # Per-category summary for this backend
        self._print_backend_summary(backend, backend_results)

        self.results[backend.tag] = backend_results
        self.backend_meta[backend.tag] = {
            "name": backend.name,
            "tag": backend.tag,
            "total_requests": backend.total_requests,
            "failed_requests": backend.failed_requests,
        }

    def _print_backend_summary(
        self, backend: TTSBackend, results: list[RunResult]
    ) -> None:
        by_cat: dict[str, list[RunResult]] = defaultdict(list)
        for r in results:
            by_cat[r.category].append(r)

        print(f"\n  {backend.name}  —  per-category summary:")
        print(
            f"  {'Category':<14} {'RTF':>8} {'Avg':>8} {'Best':>8} {'P95':>8} {'StdDev':>8}"
        )
        print(f"  {'-' * 14} {'-' * 8} {'-' * 8} {'-' * 8} {'-' * 8} {'-' * 8}")

        for cat, cat_results in by_cat.items():
            all_times = [t for r in cat_results for t in r.times]
            if not all_times:
                continue
            avg_rtf = statistics.mean(
                [r.rtf for r in cat_results if r.rtf > 0]
            )
            avg_time = statistics.mean(all_times)
            best_time = min(all_times)
            p95_time = sorted(all_times)[int(len(all_times) * 0.95)] if len(all_times) > 1 else all_times[0]
            stddev_time = statistics.stdev(all_times) if len(all_times) > 1 else 0.0
            print(
                f"  {cat:<14} {pretty_rtf(avg_rtf):>8} {avg_time:>7.2f}s {best_time:>7.2f}s {p95_time:>7.2f}s {stddev_time:>7.2f}s"
            )

        print()

    # ── reports ──────────────────────────────────────────────────────────

    def _generate_reports(self) -> None:
        print_header("COMPARISON REPORT", 80)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tags = [t for t in ["gpt_sovits_v1", "gpt_sovits_v2", "qwen3_preset", "qwen3_clone"]
                if t in self.results]

        # Always save individual results per backend
        for tag in tags:
            meta = self.backend_meta[tag]
            out_json = self.output_dir / f"{tag}_{timestamp}.json"
            single = {
                "timestamp": timestamp,
                "backend": tag,
                "name": meta["name"],
                "total_requests": meta["total_requests"],
                "failed_requests": meta["failed_requests"],
                "results": [asdict(r) for r in self.results[tag]],
            }
            with open(out_json, "w") as f:
                json.dump(single, f, indent=2)
            print(f"  {meta['name']}: {out_json.name}")

        if len(tags) < 2:
            print(f"\n  Run more backends for comparison. Results in: {self.output_dir}")
            return

        # ── side-by-side table ─────────────────────────────────────────
        cats = list(SENTENCE_CATALOG.keys())

        # Build combined rows
        combined_rows: dict[str, dict[str, dict]] = {}
        for tag in tags:
            for r in self.results[tag]:
                key = r.label
                if key not in combined_rows:
                    combined_rows[key] = {}
                combined_rows[key][tag] = r

        print()
        header_cols = "".join(
            f"{self.backend_meta[t]['name']:<30}" for t in tags
        )
        print(f"  {'Category':<12} {header_cols}")
        print(f"  {'':12} " + "".join(f"{'RTF':>6} {'Avg(s)':>7} {'P95(s)':>7} {'':>8}" for _ in tags))
        print(f"  {'-' * 12} " + "".join(f"{'':->28}" for _ in tags))

        # Per-category averages
        overall_cols: dict[str, list[float]] = defaultdict(list)
        for cat in cats:
            cat_data: dict[str, tuple] = {}
            for tag in tags:
                cat_results = [r for r in self.results[tag] if r.category == cat]
                if not cat_results:
                    continue
                all_times = [t for r in cat_results for t in r.times]
                if not all_times:
                    continue
                avg_rtf = statistics.mean([r.rtf for r in cat_results if r.rtf > 0])
                avg_time = statistics.mean(all_times)
                p95_t = sorted(all_times)[int(len(all_times) * 0.95)] if len(all_times) > 1 else all_times[0]
                cat_data[tag] = (avg_rtf, avg_time, p95_t)
                overall_cols[tag].extend(all_times)

            row = ""
            for tag in tags:
                if tag in cat_data:
                    rtf, avg, p95 = cat_data[tag]
                    row += f"{rtf:>5.2f}x {avg:>6.2f}s {p95:>6.2f}s  "
                else:
                    row += f"{'':>6} {'':>7} {'':>7}  "
            print(f"  {cat:<12} {row}")

        # Overall row
        print(f"  {'=' * 12} " + "".join(f"{'=':->28}" for _ in tags))
        overall_row = ""
        for tag in tags:
            if tag in overall_cols and overall_cols[tag]:
                ts = overall_cols[tag]
                win_rtf = statistics.mean(ts) / 5.0  # rough avg RTF
                overall_row += (
                    f"{statistics.mean(ts):>6.2f}s {min(ts):>6.2f}s "
                    f"{statistics.stdev(ts) if len(ts)>1 else 0:>5.2f}s "
                )
            else:
                overall_row += f"{'':>6} {'':>7} {'':>7}  "
        print(f"  {'OVERALL':<12} {overall_row}")
        print(f"  {'':12} " + "".join(f"{'mean':>6} {'best':>7} {'std':>7}  " for _ in tags))

        # ── winner per category ────────────────────────────────────────
        print(f"\n  Winner per category (by RTF):")
        for cat in cats:
            best_tag = None
            best_rtf = float("inf")
            for tag in tags:
                cat_results = [r for r in self.results[tag] if r.category == cat]
                if cat_results:
                    rtf = statistics.mean([r.rtf for r in cat_results if r.rtf > 0])
                    if rtf < best_rtf:
                        best_rtf = rtf
                        best_tag = tag
            if best_tag:
                print(f"    {cat:<14} → {self.backend_meta[best_tag]['name']} ({best_rtf:.2f}x RTF)")

        # ── overall winner ─────────────────────────────────────────────
        overall_rtfs = {}
        for tag in tags:
            all_rtfs = []
            for r in self.results.get(tag, []):
                if r.rtf > 0:
                    all_rtfs.append(r.rtf)
            if all_rtfs:
                overall_rtfs[tag] = statistics.mean(all_rtfs)
        if overall_rtfs:
            winner = min(overall_rtfs, key=overall_rtfs.get)  # type: ignore[arg-type]
            print(f"\n  🏆 OVERALL WINNER: {self.backend_meta[winner]['name']} "
                  f"({overall_rtfs[winner]:.2f}x RTF)")

        # ── JSON export ────────────────────────────────────────────────
        json_path = self.output_dir / f"compare_{timestamp}.json"
        json_report = {
            "timestamp": timestamp,
            "args": vars(self.args),
            "reference": {
                "audio": self.args.ref_audio,
                "text": self.args.ref_text,
                "lang": self.args.ref_lang,
            },
            "backends": {
                tag: {
                    "name": meta["name"],
                    "total_requests": meta["total_requests"],
                    "failed_requests": meta["failed_requests"],
                    "success_rate": (
                        (meta["total_requests"] - meta["failed_requests"])
                        / meta["total_requests"]
                    )
                    if meta["total_requests"] > 0
                    else 0,
                }
                for tag, meta in self.backend_meta.items()
            },
            "results": {
                tag: [asdict(r) for r in results]
                for tag, results in self.results.items()
            },
        }
        with open(json_path, "w") as f:
            json.dump(json_report, f, indent=2)
        print(f"\n  JSON report : {json_path}")

        # ── CSV export ─────────────────────────────────────────────────
        csv_path = self.output_dir / f"compare_{timestamp}.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "backend", "category", "label", "text", "chars", "words",
                "audio_duration", "runs", "avg_time", "median_time", "min_time",
                "max_time", "stddev", "p95", "p99", "rtf", "failures",
            ])
            for tag, results in self.results.items():
                for r in results:
                    writer.writerow([
                        tag, r.category, r.label, r.text[:60], r.chars, r.words,
                        f"{r.audio_duration:.3f}", r.successful_runs,
                        f"{r.avg_time:.3f}", f"{r.median_time:.3f}",
                        f"{r.min_time:.3f}", f"{r.max_time:.3f}",
                        f"{r.stddev:.3f}", f"{r.p95:.3f}", f"{r.p99:.3f}",
                        f"{r.rtf:.3f}", r.failures,
                    ])
        print(f"  CSV report  : {csv_path}")

        # ── Human-readable report ──────────────────────────────────────
        txt_path = self.output_dir / f"report_{timestamp}.txt"
        with open(txt_path, "w") as f:
            f.write(f"GPT-SoVITS vs Qwen3-TTS — Benchmark Report\n")
            f.write(f"{'=' * 60}\n")
            f.write(f"Timestamp : {timestamp}\n")
            f.write(f"Ref audio : {self.args.ref_audio}\n")
            f.write(f"Ref text  : {self.args.ref_text}\n")
            f.write(f"Runs/sent : {self.run_count}\n\n")

            for tag in tags:
                meta = self.backend_meta[tag]
                f.write(f"Backend: {meta['name']}\n")
                f.write(f"  Requests: {meta['total_requests']} total, "
                        f"{meta['failed_requests']} failed\n")

                all_times = [t for r in self.results[tag] for t in r.times]
                if all_times:
                    f.write(f"  Mean  : {statistics.mean(all_times):.2f}s\n")
                    f.write(f"  Median: {statistics.median(all_times):.2f}s\n")
                    f.write(f"  Min   : {min(all_times):.2f}s\n")
                    f.write(f"  Max   : {max(all_times):.2f}s\n")
                f.write("\n")

            if overall_rtfs:
                f.write("Overall RTF Comparison:\n")
                for tag in tags:
                    if tag in overall_rtfs:
                        f.write(f"  {self.backend_meta[tag]['name']:<24} "
                                f"{overall_rtfs[tag]:.2f}x\n")
                winner_tag = min(overall_rtfs, key=overall_rtfs.get)  # type: ignore[arg-type]
                f.write(f"\nOVERALL WINNER: {self.backend_meta[winner_tag]['name']} "
                        f"({overall_rtfs[winner_tag]:.2f}x RTF)\n")

        print(f"  TXT report  : {txt_path}")
        print(f"\n{'=' * 80}")
        print(f"  Benchmark complete.  Results in: {self.output_dir}")
        print(f"{'=' * 80}\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="GPT-SoVITS v1/v2 vs Qwen3-TTS comparative benchmark"
    )

    # Reference audio
    parser.add_argument(
        "--ref_audio", required=True, help="Absolute path to reference WAV"
    )
    parser.add_argument(
        "--ref_text", required=True, help="Transcript of reference audio"
    )
    parser.add_argument(
        "--ref_lang", default="en", help="Reference audio language (default: en)"
    )
    parser.add_argument(
        "--text_lang", default=None, help="Synthesis text language (default: ref_lang)"
    )

    # Server addresses
    parser.add_argument(
        "--gptsovits_host", default="127.0.0.1", help="GPT-SoVITS server host"
    )
    parser.add_argument(
        "--gptsovits_port", default=9880, type=int, help="GPT-SoVITS server port"
    )
    parser.add_argument(
        "--qwen3_host", default="127.0.0.1", help="Qwen3-TTS server host"
    )
    parser.add_argument(
        "--qwen3_port", default=8880, type=int, help="Qwen3-TTS server port"
    )

    # Benchmark settings
    parser.add_argument(
        "--runs", default=5, type=int, help="Number of timed runs per sentence"
    )
    parser.add_argument(
        "--output",
        default="./benchmark_results/gpt so vits vs qwen 3",
        help="Output directory for results",
    )

    # Selective backends
    parser.add_argument(
        "--only",
        nargs="+",
        choices=["gpt_v1", "gpt_v2", "qwen_preset", "qwen_clone", "all"],
        default=["all"],
        help="Which backends to test (default: all)",
    )

    # Skip
    parser.add_argument(
        "--skip_long", action="store_true", help="Skip very_long category"
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="Non-interactive mode — skip prompts (start servers yourself beforehand)",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.text_lang is None:
        args.text_lang = args.ref_lang

    # Validate reference audio
    if not os.path.isabs(args.ref_audio):
        print("[ERROR] --ref_audio must be an absolute path (GPT-SoVITS requires the server to read from disk).")
        sys.exit(1)
    if not os.path.exists(args.ref_audio):
        print(f"[ERROR] Reference audio not found: {args.ref_audio}")
        sys.exit(1)

    # Optionally skip very_long
    if args.skip_long:
        SENTENCE_CATALOG.pop("very_long", None)

    # Restrict backends if --only is specified
    only = args.only
    if "all" in only:
        only = ["gpt_v1", "gpt_v2", "qwen_preset", "qwen_clone"]

    # Build benchmark
    benchmark = ComparativeBenchmark(args)

    # We need to monkey-patch to skip unrequested backends.
    # A simpler approach: override the run method's phase list.
    desired_backends = set(only)

    if "gpt_v1" not in desired_backends:
        orig_run_backend = benchmark._run_backend
        benchmark._skip_gpt_v1 = True

    if "gpt_v2" not in desired_backends:
        benchmark._skip_gpt_v2 = True

    # Use a selective run
    _run_selective(benchmark, args, desired_backends)


def _run_selective(
    benchmark: ComparativeBenchmark, args, desired: set
) -> None:
    print_header("GPT-SoVITS vs Qwen3-TTS — Comparative Benchmark")

    ref_path = Path(args.ref_audio)
    print(f"  Reference audio  : {ref_path}")
    print(f"  Reference text   : {args.ref_text[:60]}...")
    print(f"  Runs per sentence: {args.runs}")
    print(f"  Output directory : {args.output}")
    print()

    total_sentences = sum(len(v) for v in SENTENCE_CATALOG.values())

    backends_to_run: list[tuple[str, TTSBackend, str | None, str | None, str | None]] = []

    if "gpt_v1" in desired:
        backends_to_run.append((
            "gpt_sovits_v1",
            GPTSovitsV1Backend(args),
            (
                "conda activate GPTSoVits\n"
                f"  cd /mnt/storage/GPT-SoVITS\n"
                "  PYTORCH_ROCM_ARCH=gfx1200 PYTHONPATH=/mnt/storage/GPT-SoVITS "
                f"python api_v2.py -a 0.0.0.0 -p {args.gptsovits_port}"
            ),
            None,
            None,
        ))

    if "gpt_v2" in desired:
        backends_to_run.append((
            "gpt_sovits_v2",
            GPTSovitsV2Backend(args),
            (
                "conda activate GPTSoVits\n"
                f"  cd /mnt/storage/gpt-sovits-2\n"
                "  PYTORCH_ROCM_ARCH=gfx1200 PYTHONPATH=/mnt/storage/gpt-sovits-2 "
                f"python api_v2_cache.py -a 0.0.0.0 -p {args.gptsovits_port}"
            ),
            "Stop the GPT-SoVITS v1 server first, then start v2.",
            None,
        ))

    if "qwen_preset" in desired:
        backends_to_run.append((
            "qwen3_preset",
            Qwen3PresetBackend(args),
            None,
            None,
            None,
        ))

    if "qwen_clone" in desired:
        backends_to_run.append((
            "qwen3_clone",
            Qwen3CloneBackend(args),
            None,
            None,
            None,
        ))

    total_phases = len(backends_to_run)

    for i, (tag, backend, start_cmd, stop_msg, _) in enumerate(backends_to_run, 1):
        # Determine stop_msg for GPT-SoVITS transitions
        current_stop = stop_msg
        if (tag == "gpt_sovits_v2" and "gpt_v1" in desired
                and i > 1 and backends_to_run[i - 2][0] == "gpt_sovits_v1"):
            current_stop = "Stop the GPT-SoVITS v1 server first, then start v2."
        elif tag == "gpt_sovits_v2" and "gpt_v1" not in desired:
            current_stop = None

        benchmark._run_backend(
            backend, total_sentences,
            phase=i, total_phases=total_phases,
            start_cmd=start_cmd,
            stop_msg=current_stop,
        )

    benchmark._generate_reports()


if __name__ == "__main__":
    main()
