import asyncio
import json
import websockets
from typing import Optional

from avatar.emotion_map import MoodConfig
from utils.logging import logger


class VTubeController:
    def __init__(self, config: MoodConfig) -> None:
        self.config = config
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.authenticated = False
        self._request_id = 0
        self._current_mood: Optional[str] = None
        self._reconnect_task: Optional[asyncio.Task] = None

    def _next_id(self) -> str:
        self._request_id += 1
        return f"kuro_{self._request_id}"

    async def ensure_connected(self) -> bool:
        if self.ws and self.authenticated:
            return True
        return await self.connect()

    async def connect(self) -> bool:
        uri = f"ws://{self.config.vts_host}:{self.config.vts_port}"
        try:
            self.ws = await websockets.connect(uri, max_size=2 ** 20, ping_interval=None)
            await self._authenticate()
            logger.info("Connected to VTube Studio")
            return True
        except (ConnectionRefusedError, OSError, websockets.WebSocketException) as e:
            logger.info("VTube Studio not available: %s", e)
            self.ws = None
            return False

    async def _authenticate(self) -> None:
        msg = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": self._next_id(),
            "messageType": "AuthenticationRequest",
            "data": {
                "pluginName": self.config.plugin_name,
                "pluginDeveloper": self.config.plugin_developer,
            },
        }
        await self.ws.send(json.dumps(msg))
        resp = json.loads(await self.ws.recv())
        data = resp.get("data", {})

        if data.get("authenticated"):
            self.authenticated = True
            return

        token = data.get("authenticationToken")
        if not token:
            logger.warning("VTS auth response missing token")
            return

        confirm = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": self._next_id(),
            "messageType": "AuthenticationTokenRequest",
            "data": {
                "pluginName": self.config.plugin_name,
                "pluginDeveloper": self.config.plugin_developer,
                "authenticationToken": token,
            },
        }
        await self.ws.send(json.dumps(confirm))

        try:
            ack = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=30))
            self.authenticated = ack.get("data", {}).get("authenticated", False)
            if self.authenticated:
                logger.info("VTube Studio plugin approved")
            else:
                logger.info("VTube Studio plugin awaiting approval — check VTS popup")
        except asyncio.TimeoutError:
            logger.warning("VTS auth timeout — plugin may need manual approval")

    async def set_mood(self, mood: str) -> None:
        if mood == self._current_mood:
            return
        self._current_mood = mood
        await self._trigger_hotkey(self.config.get_expression(mood))

    async def _trigger_hotkey(self, hotkey_id: str) -> None:
        if not await self.ensure_connected():
            return
        msg = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": self._next_id(),
            "messageType": "HotkeyTriggerRequest",
            "data": {"hotkeyID": hotkey_id},
        }
        try:
            await self.ws.send(json.dumps(msg))
        except websockets.WebSocketException as e:
            logger.warning("VTS send failed: %s", e)
            self.ws = None
            self.authenticated = False

    async def trigger_idle(self) -> None:
        anim = self.config.random_idle()
        if anim:
            await self._trigger_hotkey(anim)

    async def list_models(self) -> list[dict]:
        if not await self.ensure_connected():
            return []
        msg = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": self._next_id(),
            "messageType": "AvailableModelsRequest",
            "data": {},
        }
        try:
            await self.ws.send(json.dumps(msg))
            resp = json.loads(await self.ws.recv())
            return resp.get("data", {}).get("availableModels", [])
        except (websockets.WebSocketException, json.JSONDecodeError) as e:
            logger.warning("VTS list_models failed: %s", e)
            return []

    async def load_model(self, model_id: str) -> bool:
        if not await self.ensure_connected():
            return False
        msg = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": self._next_id(),
            "messageType": "ModelLoadRequest",
            "data": {"modelID": model_id},
        }
        try:
            await self.ws.send(json.dumps(msg))
            resp = json.loads(await self.ws.recv())
            success = resp.get("data", {}).get("modelLoaded", False)
            if success:
                logger.info("Loaded model: %s", model_id)
            return success
        except (websockets.WebSocketException, json.JSONDecodeError) as e:
            logger.warning("VTS load_model failed: %s", e)
            return False

    async def close(self) -> None:
        if self.ws:
            await self.ws.close()
            self.ws = None
        self.authenticated = False
