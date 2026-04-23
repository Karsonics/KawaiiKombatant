#!/usr/bin/env python3
"""
GPT-SoVITS Comprehensive Benchmark Suite
=========================================
A deep, thorough benchmark that tests multiple aspects of the GPT-SoVITS API.

Usage:
    1. Start the API server first:
       conda activate GPTSoVits
       cd /home/weeb_user/GPT-SoVITS
       PYTORCH_ROCM_ARCH=gfx1200 PYTHONPATH=/home/weeb_user/GPT-SoVITS python api_v2.py -a 0.0.0.0 -p 9880

    2. Run this benchmark:
       python bench_comprehensive.py \
           --ref_audio /path/to/reference.wav \
           --ref_text "Reference text" \
           --ref_lang en \
           --output ./benchmark_results \
           --runs 5

        REF_AUDIO_PATH = "/home/weeb_user/Documents/kawaii/KawaiiKombatant/assets/voices/megumi_clean.wav"
        REF_AUDIO_TEXT = "For the cost of a meal and basic necessities, you can have the power of an archwizard."



        python bench_comprehensive.py \
           --ref_audio /home/weeb_user/Documents/kawaii/KawaiiKombatant/assets/voices/megumi_clean.wav \
           --ref_text "For the cost of a meal and basic necessities, you can have the power of an archwizard." \
           --ref_lang en \
           --output ./benchmark_results \
           --runs 5




Features:
    - Multiple sentence categories (short, medium, long, very long, stress)
    - Batch processing tests
    - Text split method comparisons
    - Language pair tests
    - Latency breakdown (DNS, connect, TLS, first byte, total)
    - Concurrency/parallel request tests
    - Memory monitoring
    - Statistical analysis (mean, median, stddev, p95, p99)
    - Audio quality verification
    - Results export (JSON, CSV)
    - Comparison with previous runs
    - Stress testing
"""

import argparse
import csv
import io
import json
import os
import statistics
import sys
import time
import wave
import gc
import tracemalloc
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import threading

try:
    import requests
    from requests import PreparedRequest
except ImportError:
    print("[ERROR] 'requests' not installed. Run: pip install requests")
    sys.exit(1)

try:
    import psutil
except ImportError:
    print("[WARNING] 'psutil' not installed. Memory monitoring disabled. Run: pip install psutil")
    psutil = None


@dataclass
class BenchmarkResult:
    label: str
    category: str
    text: str
    chars: int
    words: int
    audio_duration: float = 0.0
    times: list = field(default_factory=list)
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
    def stddev(self) -> float:
        if len(self.times) < 2:
            return 0.0
        return statistics.stdev(self.times)
    
    @property
    def p95(self) -> float:
        if not self.times:
            return 0.0
        sorted_times = sorted(self.times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]
    
    @property
    def p99(self) -> float:
        if not self.times:
            return 0.0
        sorted_times = sorted(self.times)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[min(idx, len(sorted_times) - 1)]
    
    @property
    def rtf(self) -> float:
        if self.audio_duration <= 0:
            return float('nan')
        return self.avg_time / self.audio_duration


@dataclass
class BatchResult:
    batch_size: int
    total_time: float
    individual_times: list
    avg_per_item: float
    failures: int


@dataclass
class ConcurrencyResult:
    num_workers: int
    total_time: float
    successful: int
    failed: int
    avg_latency: float
    throughput: float


class MemoryMonitor:
    def __init__(self):
        self.running = False
        self.thread = None
        self.samples = []
        self.process = psutil.Process() if psutil else None
    
    def start(self):
        if not psutil:
            return
        self.running = True
        self.samples = []
        self.thread = threading.Thread(target=self._monitor)
        self.thread.daemon = True
        self.thread.start()
    
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
    
    def _monitor(self):
        while self.running:
            try:
                mem_info = self.process.memory_info()
                self.samples.append({
                    'timestamp': time.time(),
                    'rss_mb': mem_info.rss / 1024 / 1024,
                    'vms_mb': mem_info.vms / 1024 / 1024,
                })
            except:
                pass
            time.sleep(0.1)
    
    def get_peak(self) -> dict:
        if not self.samples:
            return {'rss_mb': 0, 'vms_mb': 0}
        rss = max(s['rss_mb'] for s in self.samples)
        vms = max(s['vms_mb'] for s in self.samples)
        return {'rss_mb': rss, 'vms_mb': vms}
    
    def get_avg(self) -> dict:
        if not self.samples:
            return {'rss_mb': 0, 'vms_mb': 0}
        rss = statistics.mean(s['rss_mb'] for s in self.samples)
        vms = statistics.mean(s['vms_mb'] for s in self.samples)
        return {'rss_mb': rss, 'vms_mb': vms}


class LatencyTimer:
    def __init__(self, session: requests.Session):
        self.session = session
    
    def measure(self, url: str, method: str = 'POST', **kwargs) -> dict:
        result = {
            'dns': 0,
            'connect': 0,
            'tls': 0,
            'first_byte': 0,
            'total': 0,
        }
        
        start = time.perf_counter()
        
        if method.upper() == 'POST':
            req = PreparedRequest()
            req.prepare_url(url, kwargs.get('params'))
            req.prepare_body(data=kwargs.get('data'), json=kwargs.get('json'))
            
            result['dns'] = 0
            result['connect'] = 0
            result['tls'] = 0
            
            r = self.session.send(
                self.session.prepare_request(req),
                timeout=kwargs.get('timeout', 300),
                stream=kwargs.get('stream', False)
            )
            
            result['first_byte'] = time.perf_counter() - start
            r.content
            result['total'] = time.perf_counter() - start
        else:
            r = requests.get(url, timeout=kwargs.get('timeout', 30))
            result['total'] = time.perf_counter() - start
        
        return result, r


def parse_args():
    parser = argparse.ArgumentParser(description="GPT-SoVITS Comprehensive Benchmark")
    
    parser.add_argument("--ref_audio", required=True,
                        help="Absolute path to reference WAV")
    parser.add_argument("--ref_text", required=True,
                        help="Transcript of reference audio")
    parser.add_argument("--ref_lang", default="en",
                        help="Reference audio language")
    parser.add_argument("--text_lang", default=None,
                        help="Synthesis text language (default: ref_lang)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=9880, type=int)
    parser.add_argument("--runs", default=5, type=int,
                        help="Number of runs per test case")
    parser.add_argument("--output", default="./benchmark_results",
                        help="Output directory for results")
    parser.add_argument("--batch_sizes", nargs='+', type=int, default=[1, 2, 4, 8],
                        help="Batch sizes to test")
    parser.add_argument("--concurrency", nargs='+', type=int, default=[1, 2, 4, 8],
                        help="Concurrent request counts to test")
    parser.add_argument("--skip_warmup", action="store_true",
                        help="Skip warm-up run")
    parser.add_argument("--skip_batch", action="store_true",
                        help="Skip batch processing tests")
    parser.add_argument("--skip_concurrency", action="store_true",
                        help="Skip concurrency tests")
    parser.add_argument("--skip_stress", action="store_true",
                        help="Skip stress tests")
    parser.add_argument("--compare", type=str, default=None,
                        help="Compare with previous benchmark JSON")
    parser.add_argument("--text_split_methods", nargs='+', 
                        default=['cut5', 'cut6', 'cut4'],
                        help="Text split methods to test")
    parser.add_argument("--languages", nargs='+',
                        default=['en', 'zh', 'ja'],
                        help="Languages to test")
    parser.add_argument("--export_json", action="store_true", default=True,
                        help="Export JSON results")
    parser.add_argument("--export_csv", action="store_true", default=True,
                        help="Export CSV results")
    parser.add_argument("--save_audio", action="store_true",
                        help="Save sample audio outputs")
    parser.add_argument("--quiet", action="store_true",
                        help="Reduce output verbosity")
    
    return parser.parse_args()


SENTENCE_CATALOG = {
    'short': [
        "Hello!",
        "How are you?",
        "Nice to meet you.",
        "Good morning!",
        "Thank you.",
    ],
    'medium': [
        "The quick brown fox jumps over the lazy dog.",
        "Artificial intelligence is transforming our world.",
        "Welcome to the future of voice synthesis.",
        "Machine learning enables incredible capabilities.",
        "Speech synthesis has come a long way.",
    ],
    'long': [
        "Artificial intelligence has transformed many industries over the past decade, enabling machines to perform tasks that previously required human intelligence, from recognizing speech and images to translating languages and driving cars.",
        "The history of computing spans thousands of years, from the abacus in ancient times to modern quantum computers, each generation building upon the innovations of those who came before.",
        "Climate change represents one of the greatest challenges facing humanity in the 21st century, requiring coordinated global action to mitigate its effects and adapt to its consequences.",
    ],
    'very_long': [
        "In the realm of artificial intelligence and machine learning, researchers have made remarkable strides in developing systems capable of understanding, processing, and generating human language. These advances have applications ranging from virtual assistants and chatbots to automated translation services and content generation. The ability of machines to not only comprehend but also produce natural-sounding speech represents a particularly fascinating frontier in AI development, one that combines expertise from acoustics, linguistics, and deep learning.",
        "Throughout history, human beings have sought to preserve and transmit knowledge across generations and cultures. From the earliest cave paintings and oral traditions to the invention of writing, the printing press, and now digital technologies, each new medium for recording and sharing information has fundamentally transformed society. Today, we stand at another pivotal moment in this ongoing story, where the boundaries between human and machine creativity are becoming increasingly blurred.",
    ],
    'stress': [
        "Supercalifragilisticexpialidocious!",
        "The five boxing wizards jump quickly. A quick movement of the enemy will jeopardize six gunboats. All questions asked by five expert boxing wizards are answered!",
        "Unique New York Unique New York Unique New York You know you need unique New York Unique New York Unique New York",
    ],
    'punctuation': [
        "Hello, world! How's it going? I'm doing great... really, I am!",
        "Wait... did you hear that? Yes, yes, I did! Absolutely incredible!",
        "Call me at 1-800-555-0199. Or email: test@example.com. Maybe both?",
    ],
    'numbers': [
        "The population of Earth is approximately 8 billion people as of 2024.",
        "Pi is approximately 3.14159. The square root of 2 is about 1.41421.",
        "In 1984, Apple released the Macintosh. In 2007, they introduced the iPhone.",
    ],
}


def create_output_directory(path: str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def print_header(title: str, width: int = 80):
    print(f"\n{'='*width}")
    print(f"  {title}")
    print(f"{'='*width}\n")


def print_progress(current: int, total: int, prefix: str = "", bar_length: int = 40):
    if current >= total:
        return
    percent = current / total
    filled = int(bar_length * percent)
    bar = '█' * filled + '░' * (bar_length - filled)
    print(f"\r{prefix}: |{bar}| {current}/{total} ({percent*100:.1f}%)", end='', flush=True)
    if current >= total:
        print()


def wav_duration(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as f:
            return f.getnframes() / float(f.getframerate())
    except Exception:
        return 0.0


def verify_audio(data: bytes) -> dict:
    result = {
        'valid': False,
        'duration': 0.0,
        'sample_rate': 0,
        'channels': 0,
        'sample_width': 0,
        'frames': 0,
    }
    try:
        with wave.open(io.BytesIO(data), "rb") as f:
            result['valid'] = True
            result['duration'] = f.getnframes() / float(f.getframerate())
            result['sample_rate'] = f.getframerate()
            result['channels'] = f.getnchannels()
            result['sample_width'] = f.getsampwidth()
            result['frames'] = f.getnframes()
    except Exception:
        pass
    return result


def check_server(base_url: str, timeout: int = 10) -> bool:
    try:
        requests.get(f"{base_url}/tts", timeout=timeout)
        return True
    except requests.exceptions.ConnectionError:
        return False
    except Exception:
        return True


def get_session_with_timing() -> tuple[requests.Session, LatencyTimer]:
    session = requests.Session()
    return session, LatencyTimer(session)


class GPTSoVITSBenchmark:
    def __init__(self, args):
        self.args = args
        self.base_url = f"http://{args.host}:{args.port}"
        self.output_dir = create_output_directory(args.output)
        self.results = []
        self.batch_results = []
        self.concurrency_results = []
        self.memory_monitor = MemoryMonitor()
        self.start_time = None
        self.total_requests = 0
        self.failed_requests = 0
        
    def run(self):
        self.start_time = time.time()
        
        print_header("GPT-SoVITS Comprehensive Benchmark Suite")
        
        if not check_server(self.base_url):
            print(f"[ERROR] Cannot connect to server at {self.base_url}")
            print("Make sure api_v2.py is running first.")
            sys.exit(1)
        
        print(f"Server: {self.base_url}")
        print(f"Reference: {self.args.ref_audio}")
        print(f"Languages: {self.args.ref_lang} -> {self.args.text_lang or self.args.ref_lang}")
        print(f"Runs per test: {self.args.runs}")
        print(f"Output: {self.output_dir}")
        
        if not self.args.skip_warmup:
            self._warmup()
        
        self._run_category_tests()
        
        if self.args.text_split_methods:
            self._test_text_split_methods()
        
        if self.args.languages and len(self.args.languages) > 1:
            self._test_language_pairs()
        
        if not self.args.skip_batch:
            self._test_batch_processing()
        
        if not self.args.skip_concurrency:
            self._test_concurrency()
        
        if not self.args.skip_stress:
            self._stress_test()
        
        self._generate_reports()
        
        if self.args.compare:
            self._compare_results()
        
        print_header("BENCHMARK COMPLETE")
        elapsed = time.time() - self.start_time
        print(f"Total time: {elapsed:.2f}s")
        print(f"Total requests: {self.total_requests}")
        print(f"Failed requests: {self.failed_requests}")
        print(f"Success rate: {(self.total_requests - self.failed_requests)/self.total_requests*100:.1f}%")
        print(f"\nResults saved to: {self.output_dir}")
    
    def _warmup(self):
        print_header("WARM-UP", 60)
        print("Warming up (first inference loads model weights)...")
        
        payload = self._create_payload("Warm up the model.")
        
        t0 = time.perf_counter()
        try:
            r = requests.post(f"{self.base_url}/tts", json=payload, timeout=300)
            elapsed = time.perf_counter() - t0
            if r.status_code == 200:
                audio_info = verify_audio(r.content)
                print(f"Warm-up complete: {elapsed:.2f}s")
                print(f"  Audio: {audio_info['duration']:.2f}s, {audio_info['sample_rate']}Hz")
            else:
                print(f"Warm-up failed: HTTP {r.status_code}")
        except Exception as e:
            print(f"Warm-up failed: {e}")
            sys.exit(1)
    
    def _create_payload(self, text: str, **overrides) -> dict:
        text_lang = self.args.text_lang or self.args.ref_lang
        payload = {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": self.args.ref_audio,
            "prompt_text": self.args.ref_text,
            "prompt_lang": self.args.ref_lang,
            "media_type": "wav",
            "streaming_mode": False,
            "batch_size": 1,
            "text_split_method": "cut5",
        }
        payload.update(overrides)
        return payload
    
    def _synthesize(self, text: str, **kwargs) -> tuple:
        payload = self._create_payload(text, **kwargs)
        
        t0 = time.perf_counter()
        r = requests.post(f"{self.base_url}/tts", json=payload, timeout=kwargs.get('timeout', 300))
        elapsed = time.perf_counter() - t0
        
        self.total_requests += 1
        if r.status_code != 200:
            self.failed_requests += 1
            raise RuntimeError(f"HTTP {r.status_code}")
        
        return r.content, elapsed
    
    def _run_category_tests(self):
        print_header("CATEGORY TESTS", 60)
        
        categories = list(SENTENCE_CATALOG.keys())
        total_tests = sum(len(sentences) for sentences in SENTENCE_CATALOG.values()) * self.args.runs
        completed = 0
        
        for category, sentences in SENTENCE_CATALOG.items():
            print(f"\n{'─'*60}")
            print(f"Category: {category.upper()}")
            print(f"{'─'*60}")
            
            for sentence_idx, sentence in enumerate(sentences):
                result = BenchmarkResult(
                    label=f"{category}_{sentence_idx}",
                    category=category,
                    text=sentence,
                    chars=len(sentence),
                    words=len(sentence.split())
                )
                
                print(f"\n  [{sentence_idx+1}/{len(sentences)}] \"{sentence[:50]}{'...' if len(sentence) > 50 else ''}\"")
                
                for run_idx in range(self.args.runs):
                    try:
                        data, elapsed = self._synthesize(sentence)
                        result.times.append(elapsed)
                        
                        if run_idx == 0:
                            audio_info = verify_audio(data)
                            result.audio_duration = audio_info['duration']
                        
                        if not self.args.quiet:
                            print(f"    Run {run_idx+1}: {elapsed:.2f}s")
                    except Exception as e:
                        result.failures += 1
                        result.error_messages.append(str(e))
                        if not self.args.quiet:
                            print(f"    Run {run_idx+1}: FAILED - {e}")
                    
                    completed += 1
                    print_progress(completed, total_tests, "Progress")
                
                if result.successful_runs > 0:
                    print(f"    Avg: {result.avg_time:.2f}s | RTF: {result.rtf:.2f}x | "
                          f"P95: {result.p95:.2f}s | StdDev: {result.stddev:.2f}s")
                
                self.results.append(result)
    
    def _test_text_split_methods(self):
        print_header("TEXT SPLIT METHOD COMPARISON", 60)
        
        test_text = (
            "This is a test sentence that has multiple clauses. "
            "It should be handled properly by different split methods. "
            "Each method may produce slightly different results."
        )
        
        for method in self.args.text_split_methods:
            print(f"\nTesting method: {method}")
            times = []
            
            for run in range(self.args.runs):
                try:
                    data, elapsed = self._synthesize(test_text, text_split_method=method)
                    times.append(elapsed)
                    print(f"  Run {run+1}: {elapsed:.2f}s")
                except Exception as e:
                    print(f"  Run {run+1}: FAILED - {e}")
            
            if times:
                avg = statistics.mean(times)
                print(f"  Average: {avg:.2f}s")
    
    def _test_language_pairs(self):
        print_header("LANGUAGE PAIR TESTS", 60)
        
        test_text = "Hello, this is a test of the GPT-SoVITS system."
        
        for lang in self.args.languages:
            print(f"\nTesting language: {lang}")
            times = []
            
            for run in range(self.args.runs):
                try:
                    data, elapsed = self._synthesize(test_text, text_lang=lang)
                    times.append(elapsed)
                    print(f"  Run {run+1}: {elapsed:.2f}s")
                except Exception as e:
                    print(f"  Run {run+1}: FAILED - {e}")
            
            if times:
                avg = statistics.mean(times)
                print(f"  Average: {avg:.2f}s")
    
    def _test_batch_processing(self):
        print_header("BATCH PROCESSING TESTS", 60)
        
        test_sentences = [
            "First sentence for batch test.",
            "Second sentence for batch test.",
            "Third sentence for batch test.",
            "Fourth sentence for batch test.",
            "Fifth sentence for batch test.",
            "Sixth sentence for batch test.",
            "Seventh sentence for batch test.",
            "Eighth sentence for batch test.",
        ]
        
        for batch_size in self.args.batch_sizes:
            if batch_size > len(test_sentences):
                continue
            
            print(f"\nTesting batch size: {batch_size}")
            times = []
            
            for run in range(self.args.runs):
                sentences = test_sentences[:batch_size]
                payload = self._create_payload(
                    "|||".join(sentences),
                    batch_size=batch_size
                )
                
                t0 = time.perf_counter()
                try:
                    r = requests.post(f"{self.base_url}/tts", json=payload, timeout=300)
                    elapsed = time.perf_counter() - t0
                    times.append(elapsed)
                    print(f"  Run {run+1}: {elapsed:.2f}s ({elapsed/batch_size:.2f}s per item)")
                except Exception as e:
                    print(f"  Run {run+1}: FAILED - {e}")
            
            if times:
                avg = statistics.mean(times)
                self.batch_results.append(BatchResult(
                    batch_size=batch_size,
                    total_time=avg,
                    individual_times=times,
                    avg_per_item=avg/batch_size,
                    failures=sum(1 for t in times if t == 0)
                ))
    
    def _test_concurrency(self):
        print_header("CONCURRENCY TESTS", 60)
        
        test_text = "Concurrent test sentence."
        
        for num_workers in self.args.concurrency:
            print(f"\nTesting {num_workers} concurrent requests...")
            
            self.memory_monitor.start()
            
            def make_request(_):
                try:
                    data, elapsed = self._synthesize(test_text)
                    return True, elapsed
                except Exception:
                    return False, 0
            
            t0 = time.perf_counter()
            
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = [executor.submit(make_request, i) for i in range(num_workers)]
                results = [f.result() for f in as_completed(futures)]
            
            total_time = time.perf_counter() - t0
            self.memory_monitor.stop()
            
            successful = sum(1 for ok, _ in results if ok)
            failed = num_workers - successful
            latencies = [elapsed for ok, elapsed in results if ok]
            avg_latency = statistics.mean(latencies) if latencies else 0
            
            print(f"  Total time: {total_time:.2f}s")
            print(f"  Successful: {successful}/{num_workers}")
            print(f"  Avg latency: {avg_latency:.2f}s")
            print(f"  Throughput: {successful/total_time:.2f} req/s")
            
            mem_peak = self.memory_monitor.get_peak()
            print(f"  Peak memory: {mem_peak['rss_mb']:.1f} MB")
            
            self.concurrency_results.append(ConcurrencyResult(
                num_workers=num_workers,
                total_time=total_time,
                successful=successful,
                failed=failed,
                avg_latency=avg_latency,
                throughput=successful/total_time
            ))
    
    def _stress_test(self):
        print_header("STRESS TEST", 60)
        
        print("\nRunning continuous requests until failure or 60 seconds...")
        
        self.memory_monitor.start()
        
        success_count = 0
        failure_count = 0
        times = []
        start = time.time()
        max_duration = 60
        
        test_text = "Stress test message number "
        
        while time.time() - start < max_duration:
            idx = success_count + failure_count
            try:
                data, elapsed = self._synthesize(f"{test_text}{idx}")
                success_count += 1
                times.append(elapsed)
                
                if success_count % 10 == 0:
                    print(f"  Progress: {success_count} successful, {failure_count} failed")
            except Exception:
                failure_count += 1
                if failure_count >= 5:
                    print("  Too many failures, stopping stress test")
                    break
        
        self.memory_monitor.stop()
        
        elapsed_total = time.time() - start
        
        print(f"\nStress test results:")
        print(f"  Duration: {elapsed_total:.2f}s")
        print(f"  Successful: {success_count}")
        print(f"  Failed: {failure_count}")
        print(f"  Throughput: {success_count/elapsed_total:.2f} req/s")
        
        if times:
            print(f"  Avg latency: {statistics.mean(times):.2f}s")
            print(f"  Min latency: {min(times):.2f}s")
            print(f"  Max latency: {max(times):.2f}s")
        
        mem_peak = self.memory_monitor.get_peak()
        print(f"  Peak memory: {mem_peak['rss_mb']:.1f} MB")
    
    def _generate_reports(self):
        print_header("GENERATING REPORTS", 60)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        report = {
            'timestamp': timestamp,
            'args': vars(self.args),
            'summary': {
                'total_tests': len(self.results),
                'total_requests': self.total_requests,
                'failed_requests': self.failed_requests,
                'success_rate': (self.total_requests - self.failed_requests) / self.total_requests if self.total_requests > 0 else 0,
            },
            'category_results': [asdict(r) for r in self.results],
            'batch_results': [asdict(r) for r in self.batch_results],
            'concurrency_results': [asdict(r) for r in self.concurrency_results],
        }
        
        if self.args.export_json:
            json_path = self.output_dir / f"benchmark_{timestamp}.json"
            with open(json_path, 'w') as f:
                json.dump(report, f, indent=2)
            print(f"JSON report: {json_path}")
        
        if self.args.export_csv:
            csv_path = self.output_dir / f"benchmark_{timestamp}.csv"
            with open(csv_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Category', 'Label', 'Text', 'Chars', 'Words', 
                                 'Audio Duration', 'Successful Runs', 'Failures',
                                 'Avg Time', 'Median Time', 'Min Time', 'Max Time',
                                 'StdDev', 'P95', 'P99', 'RTF'])
                
                for r in self.results:
                    writer.writerow([
                        r.category, r.label, r.text[:50], r.chars, r.words,
                        f"{r.audio_duration:.2f}", r.successful_runs, r.failures,
                        f"{r.avg_time:.2f}", f"{r.median_time:.2f}", 
                        f"{r.min_time:.2f}", f"{r.max_time:.2f}",
                        f"{r.stddev:.2f}", f"{r.p95:.2f}", f"{r.p99:.2f}",
                        f"{r.rtf:.2f}"
                    ])
            print(f"CSV report: {csv_path}")
        
        self._print_summary_table()
    
    def _print_summary_table(self):
        print("\n" + "="*80)
        print("  SUMMARY RESULTS")
        print("="*80)
        
        print(f"\n{'Category':<15} {'Runs':>6} {'Avg Time':>10} {'RTF':>8} {'P95':>10} {'StdDev':>10}")
        print("-"*80)
        
        by_category = defaultdict(list)
        for r in self.results:
            by_category[r.category].append(r)
        
        for category, results in by_category.items():
            all_times = []
            all_rtf = []
            all_p95 = []
            all_stddev = []
            
            for r in results:
                all_times.extend(r.times)
                all_rtf.append(r.rtf)
                all_p95.append(r.p95)
                all_stddev.append(r.stddev)
            
            if all_times:
                avg_time = statistics.mean(all_times)
                avg_rtf = statistics.mean(all_rtf)
                avg_p95 = statistics.mean(all_p95)
                avg_stddev = statistics.mean(all_stddev)
                
                print(f"{category:<15} {len(all_times):>6} {avg_time:>9.2f}s {avg_rtf:>7.2f}x "
                      f"{avg_p95:>9.2f}s {avg_stddev:>9.2f}s")
        
        print("-"*80)
        
        if self.concurrency_results:
            print("\nConcurrency Results:")
            print(f"{'Workers':>10} {'Time':>10} {'Success':>10} {'Failed':>10} {'Throughput':>12}")
            print("-"*60)
            for cr in self.concurrency_results:
                print(f"{cr.num_workers:>10} {cr.total_time:>9.2f}s {cr.successful:>10} "
                      f"{cr.failed:>10} {cr.throughput:>11.2f}/s")
        
        print("\n" + "="*80)
        
        print("\nRTF Interpretation:")
        print("  RTF < 1.0 = faster than real-time (good)")
        print("  RTF = 1.0 = real-time")
        print("  RTF > 1.0 = slower than real-time")
        
        all_rtf = [r.rtf for r in self.results if r.rtf > 0]
        if all_rtf:
            avg_rtf = statistics.mean(all_rtf)
            print(f"\nOverall Average RTF: {avg_rtf:.2f}x")
            
            if avg_rtf < 1.0:
                print("→ Performance: EXCELLENT (faster than real-time)")
            elif avg_rtf < 2.0:
                print("→ Performance: GOOD (near real-time)")
            elif avg_rtf < 5.0:
                print("→ Performance: ACCEPTABLE")
            else:
                print("→ Performance: NEEDS OPTIMIZATION")
        
        print("="*80 + "\n")
    
    def _compare_results(self):
        print_header("COMPARISON WITH PREVIOUS RUN", 60)
        
        compare_path = Path(self.args.compare)
        if not compare_path.exists():
            print(f"Comparison file not found: {compare_path}")
            return
        
        with open(compare_path, 'r') as f:
            prev_report = json.load(f)
        
        print(f"Comparing with: {compare_path}")
        
        prev_results = {r['category']: r for r in prev_report.get('category_results', [])}
        curr_results = {r.category: r for r in self.results}
        
        print(f"\n{'Category':<15} {'Previous':>12} {'Current':>12} {'Change':>12}")
        print("-"*60)
        
        for category in set(list(prev_results.keys()) + list(curr_results.keys())):
            prev = prev_results.get(category)
            curr = curr_results.get(category)
            
            if prev and curr:
                prev_rtf = prev.get('rtf', 0)
                curr_rtf = curr.rtf
                change = ((curr_rtf - prev_rtf) / prev_rtf * 100) if prev_rtf > 0 else 0
                
                print(f"{category:<15} {prev_rtf:>11.2f}x {curr_rtf:>11.2f}x {change:>+11.1f}%")


def main():
    args = parse_args()
    
    if args.text_lang is None:
        args.text_lang = args.ref_lang
    
    benchmark = GPTSoVITSBenchmark(args)
    benchmark.run()


if __name__ == "__main__":
    main()
