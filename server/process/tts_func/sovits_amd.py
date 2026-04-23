"""
GPT-SoVITS TTS client for AMD GPU
Calls the api_v2.py REST endpoint and plays audio back
"""

import requests
import sounddevice as sd
import soundfile as sf
import tempfile
import os
from pathlib import Path


SOVITS_API_URL = "http://localhost:9880/tts"

# You need a reference audio clip of your character's voice
# Record a few seconds of the voice you want, put it here
REF_AUDIO_PATH = "/home/weeb_user/Documents/kawaii/KawaiiKombatant/assets/voices/megumi_clean.wav"
REF_AUDIO_TEXT = "For the cost of a meal and basic necessities, you can have the power of an archwizard. Just give me a permanent spot in your party and I'm all yours" # what the ref audio says


def speak(text: str, lang: str = "en") -> bool:
    if not text.strip():
        return False

    try:
        params = {
    "text": text,
    "text_lang": lang,
    "ref_audio_path": REF_AUDIO_PATH,
    "prompt_text": REF_AUDIO_TEXT,
    "prompt_lang": lang,
    "media_type": "wav",
    "streaming_mode": "false",
    "speed_factor": 1.2,        # add this - speeds up speech
    "top_k": 5,                 # add this - faster inference
    "top_p": 1.0,               # add this
    "temperature": 1.0,         # add this
}

        response = requests.get(SOVITS_API_URL, params=params, timeout=60)
        response.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(response.content)
            tmp_path = tmp.name

        data, samplerate = sf.read(tmp_path)
        
        # resample to 44100 if needed
        target_sr = 44100
        if samplerate != target_sr:
            import librosa
            data = librosa.resample(data.T if data.ndim > 1 else data, 
                                   orig_sr=samplerate, target_sr=target_sr)
            if data.ndim > 1:
                data = data.T
            samplerate = target_sr

        sd.play(data, samplerate)
        sd.wait()

        os.unlink(tmp_path)
        return True

    except requests.exceptions.ConnectionError:
        print("  [TTS] GPT-SoVITS API not reachable - is api_v2.py running on port 9880?")
        return False
    except Exception as e:
        print(f"  [TTS] Error: {e}")
        return False


def check_api_available() -> bool:
    """Check if the TTS API is up."""
    try:
        requests.get("http://localhost:9880/", timeout=3)
        return True
    except Exception:
        return False