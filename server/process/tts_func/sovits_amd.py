# Backward-compatibility shim — re-exports from the new GPTSovitsBackend.
# Existing code that imports `speak` / `check_api_available` / `set_voice`
# from this module still works.  New code should use tts_func instead.
from server.process.tts_func.gpt_sovits import GPTSovitsBackend
from utils.logging import logger

_backend = GPTSovitsBackend()

speak = _backend.speak
check_api_available = _backend.check_available
set_voice = _backend.set_voice

logger.debug("sovits_amd.py loaded (compat shim → GPTSovitsBackend)")
