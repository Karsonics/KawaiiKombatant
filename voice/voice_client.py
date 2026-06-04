#!/usr/bin/env python3
import asyncio
import json
import threading
from typing import Optional

from voice.mic import MicRecorder
from voice.vad import VoiceActivityDetector
from voice.asr import WhisperTranscriber
from utils.logging import logger

try:
    from pynput import keyboard

    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False
    logger.info("pynput not available — use CLI mode (Enter to record)")


class VoiceClient:
    def __init__(
        self,
        ws_url: str = "ws://localhost:8765/ws",
        model_size: str = "base",
        vad_threshold: float = 0.5,
    ) -> None:
        self.ws_url = ws_url
        self.mic = MicRecorder()
        self.vad = VoiceActivityDetector(threshold=vad_threshold)
        self.asr = WhisperTranscriber(model_size=model_size)
        self._ws: Optional[asyncio.Queue] = None
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._session_id: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()

        import websockets

        logger.info("Connecting to KuroAPI at %s", self.ws_url)
        async with websockets.connect(self.ws_url) as ws:
            receiver_task = asyncio.create_task(self._receiver(ws))
            sender_task = asyncio.create_task(self._sender(ws))

            if HAS_PYNPUT:
                await self._run_ptt()
            else:
                await self._run_cli()

            sender_task.cancel()
            receiver_task.cancel()

    async def _receiver(self, ws) -> None:
        import websockets

        try:
            async for raw in ws:
                resp = json.loads(raw)
                rtype = resp.get("type")
                if rtype == "done":
                    data = resp["data"]
                    self._session_id = data["session_id"]
                    print(f"\nKuro: {data['text']}\n", flush=True)
                elif rtype == "error":
                    print(f"\n[Error] {resp['data']}\n", flush=True)
                elif rtype == "command_result":
                    print(f"\n{resp['data']}\n", flush=True)
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _sender(self, ws) -> None:
        while True:
            msg = await self._send_queue.get()
            await ws.send(json.dumps(msg))

    def _on_press(self, key) -> None:
        try:
            if key == keyboard.Key.space and not self.mic.is_recording:
                self.mic.start()
                print("\n[Recording... release space to stop]", flush=True)
        except AttributeError:
            pass

    def _on_release(self, key) -> None:
        try:
            if key == keyboard.Key.space and self.mic.is_recording:
                audio = self.mic.stop()
                print("[Processing...]", flush=True)
                threading.Thread(
                    target=self._process_audio,
                    args=(audio,),
                    daemon=True,
                ).start()
        except AttributeError:
            pass

    async def _run_ptt(self) -> None:
        print("\n=== Kuro Voice Client (Push-to-Talk) ===")
        print("Hold SPACE to record, release to transcribe and send")
        print("Press ESC to exit\n")

        listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        listener.start()

        try:
            while listener.is_alive():
                await asyncio.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            listener.stop()

    async def _run_cli(self) -> None:
        print("\n=== Kuro Voice Client (CLI mode) ===")
        print("Press Enter to start recording, Enter again to stop")
        print("Type 'exit' to quit\n")

        while True:
            cmd = await asyncio.get_event_loop().run_in_executor(
                None, input, "Press Enter to record (or 'exit'): "
            )
            if cmd.lower() == "exit":
                break

            self.mic.start()
            print("[Recording... press Enter to stop]", flush=True)
            await asyncio.get_event_loop().run_in_executor(None, input)
            audio = self.mic.stop()
            print("[Processing...]", flush=True)
            await self._process_audio_async(audio)

    def _process_audio(self, audio: bytes) -> None:
        if self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(
            self._process_audio_async(audio), self._loop
        )
        try:
            future.result()
        except Exception as e:
            logger.error("Audio processing error: %s", e)

    async def _process_audio_async(self, audio: bytes) -> None:
        if len(audio) == 0:
            print("[No audio captured]", flush=True)
            return

        if not self.vad.has_speech(audio):
            print("[No speech detected]", flush=True)
            return

        try:
            text = await asyncio.get_event_loop().run_in_executor(
                None, self.asr.transcribe, audio
            )
        except RuntimeError as e:
            print(f"[ASR Error] {e}", flush=True)
            return

        if not text:
            print("[Empty transcription]", flush=True)
            return

        print(f"You: {text}", flush=True)

        await self._send_queue.put(
            {
                "type": "message",
                "data": text,
                "session_id": self._session_id,
            }
        )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Kuro Voice Client")
    parser.add_argument("--host", default="localhost", help="KuroAPI host")
    parser.add_argument("--port", type=int, default=8765, help="KuroAPI port")
    parser.add_argument(
        "--model",
        default="base",
        help="Whisper model size (tiny/base/small/medium/large)",
    )
    parser.add_argument(
        "--vad-threshold", type=float, default=0.5, help="VAD sensitivity (0-1)"
    )
    args = parser.parse_args()

    client = VoiceClient(
        ws_url=f"ws://{args.host}:{args.port}/ws",
        model_size=args.model,
        vad_threshold=args.vad_threshold,
    )

    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        print("\nGoodbye!")


if __name__ == "__main__":
    main()
