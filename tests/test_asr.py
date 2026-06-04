import unittest
import numpy as np
from unittest.mock import patch, MagicMock


class TestServerASRConfig(unittest.TestCase):
    def test_load_config_defaults_without_file(self):
        from server.process.asr_func.asr_push_to_talk import CONFIG
        self.assertIn("model", CONFIG)
        self.assertIn("audio", CONFIG)
        self.assertEqual(CONFIG["model"]["size"], "base")
        self.assertEqual(CONFIG["audio"]["sample_rate"], 16000)

    def test_load_config_uses_defaults_on_missing_file(self):
        with patch("os.path.exists", return_value=False):
            from server.process.asr_func.asr_push_to_talk import _load_config
            cfg = _load_config()
            self.assertEqual(cfg["model"]["size"], "base")
            self.assertIsNone(cfg["model"]["device"])


class TestServerASRTranscribe(unittest.TestCase):
    def test_transcribe_empty_audio(self):
        from server.process.asr_func.asr_push_to_talk import transcribe
        result = transcribe(np.array([], dtype=np.float32))
        self.assertEqual(result, "")

    def test_transcribe_bytes_empty(self):
        from server.process.asr_func.asr_push_to_talk import transcribe_bytes
        result = transcribe_bytes(b"")
        self.assertEqual(result, "")

    @patch("server.process.asr_func.asr_push_to_talk._get_transcriber")
    def test_transcribe_calls_whisper(self, mock_get):
        mock_t = MagicMock()
        mock_t.transcribe.return_value = "hello world"
        mock_get.return_value = mock_t

        from server.process.asr_func.asr_push_to_talk import transcribe
        audio = np.zeros(16000, dtype=np.float32)
        result = transcribe(audio, language="en")
        self.assertEqual(result, "hello world")
        mock_t.transcribe.assert_called_once_with(audio, language="en")

    @patch("server.process.asr_func.asr_push_to_talk._get_transcriber")
    def test_transcribe_bytes_decodes_and_calls(self, mock_get):
        mock_t = MagicMock()
        mock_t.transcribe.return_value = "test transcription"
        mock_get.return_value = mock_t

        from server.process.asr_func.asr_push_to_talk import transcribe_bytes
        audio_bytes = np.zeros(16000, dtype=np.float32).tobytes()
        result = transcribe_bytes(audio_bytes, samplerate=16000, language="en")
        self.assertEqual(result, "test transcription")

    @patch("server.process.asr_func.asr_push_to_talk._get_transcriber")
    def test_transcribe_raises_when_not_available(self, mock_get):
        mock_get.return_value = None

        from server.process.asr_func.asr_push_to_talk import transcribe
        audio = np.zeros(16000, dtype=np.float32)
        with self.assertRaises(RuntimeError):
            transcribe(audio)

    @patch("server.process.asr_func.asr_push_to_talk._get_transcriber")
    def test_is_available(self, mock_get):
        from server.process.asr_func.asr_push_to_talk import is_available
        mock_get.return_value = MagicMock()
        self.assertTrue(is_available())
        mock_get.return_value = None
        self.assertFalse(is_available())


if __name__ == '__main__':
    unittest.main()
