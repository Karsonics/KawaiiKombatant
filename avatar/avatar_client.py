#!/usr/bin/env python3
import argparse
import asyncio
import json
import time
from typing import Optional

import websockets

from avatar.emotion_map import MoodConfig
from avatar.vtube_controller import VTubeController
from utils.logging import logger


class AvatarClient:
    def __init__(
        self,
        config_path: str = "configs/vtube_config.yaml",
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.config = MoodConfig(config_path)
        if host is not None:
            self.config._raw["kuro_api"]["host"] = host
        if port is not None:
            self.config._raw["kuro_api"]["port"] = port
        self.vts = VTubeController(self.config)
        self._kapi_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._last_activity: float = 0
        self._running = True

    async def run(self) -> None:
        kapi_uri = f"ws://{self.config.kapi_host}:{self.config.kapi_port}/ws"
        logger.info("Connecting to KuroAPI at %s", kapi_uri)

        try:
            self._kapi_ws = await websockets.connect(kapi_uri, ping_interval=None)
        except (ConnectionRefusedError, OSError) as e:
            logger.error("Cannot connect to KuroAPI: %s", e)
            return

        async with self._kapi_ws:
            await self._subscribe_mood()

            model_id = self.config.model_id
            if model_id:
                await self.vts.connect()
                await self.vts.load_model(model_id)

            idle_task = asyncio.create_task(self._idle_loop())
            receiver_task = asyncio.create_task(self._receiver())

            try:
                await asyncio.gather(idle_task, receiver_task)
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
            finally:
                idle_task.cancel()
                receiver_task.cancel()
                await self.vts.close()

    async def _subscribe_mood(self) -> None:
        msg = {"type": "command", "command": "subscribe_mood"}
        await self._kapi_ws.send(json.dumps(msg))
        resp = json.loads(await self._kapi_ws.recv())
        logger.info("KuroAPI: %s", resp.get("data", "connected"))

    async def _receiver(self) -> None:
        try:
            async for raw in self._kapi_ws:
                msg = json.loads(raw)
                if msg.get("type") == "mood_update":
                    data = msg["data"]
                    await self._handle_mood(data["mood"], data.get("emotion", 0.5))
                elif msg.get("type") == "error":
                    logger.warning("KuroAPI: %s", msg.get("data"))
        except websockets.WebSocketException:
            pass

    async def _handle_mood(self, mood: str, emotion: float) -> None:
        self._last_activity = time.monotonic()
        await self.vts.ensure_connected()
        await self.vts.set_mood(mood)

    async def _idle_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.config.idle_interval)
            elapsed = time.monotonic() - self._last_activity
            if elapsed >= self.config.idle_interval and self._running:
                await self.vts.trigger_idle()

    async def list_vts_models(self) -> list[dict]:
        await self.vts.connect()
        models = await self.vts.list_models()
        await self.vts.close()
        return models


def main() -> None:
    parser = argparse.ArgumentParser(description="Kuro Avatar Client")
    parser.add_argument(
        "--config", default="configs/vtube_config.yaml", help="Config file path"
    )
    parser.add_argument(
        "--list-models", action="store_true", help="List available VTS models and exit"
    )
    args = parser.parse_args()

    if args.list_models:
        models = asyncio.run(AvatarClient(args.config).list_vts_models())
        if not models:
            print("No models found (is VTube Studio running with API enabled?)")
        else:
            print("\nAvailable VTube Studio Models:")
            for m in models:
                mid = m.get("modelID", "?")
                name = m.get("modelName", "?")
                loaded = " [LOADED]" if m.get("loaded") else ""
                print(f"  ID: {mid}")
                print(f"  Name: {name}{loaded}")
                print()
        return

    client = AvatarClient(args.config)
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        print("\nAvatar client stopped")


if __name__ == "__main__":
    main()
