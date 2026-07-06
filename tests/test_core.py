import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from src import config, history, transcriber
from src.recorder import _has_speech, _speech_metrics


class ConfigTests(unittest.TestCase):
    def test_save_and_load_normalize_bad_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            with mock.patch.object(config, "CONFIG_FILE", path):
                saved = config.save({
                    "provider": "bad",
                    "model": "",
                    "history_limit": "0",
                    "auto_paste": 1,
                })

                self.assertEqual(saved["provider"], "groq")
                self.assertEqual(saved["model"], config.DEFAULTS["model"])
                self.assertEqual(saved["history_limit"], 1)
                self.assertTrue(saved["auto_paste"])
                self.assertEqual(config.load(), saved)

    def test_load_recovers_from_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text("{", encoding="utf-8")
            with mock.patch.object(config, "CONFIG_FILE", path):
                loaded = config.load()

                self.assertEqual(loaded, config.DEFAULTS)
                self.assertEqual(json.loads(path.read_text()), config.DEFAULTS)

    def test_provider_switch_uses_matching_default_model(self):
        self.assertEqual(
            config.normalize({
                "provider": "openai",
                "model": config.DEFAULT_MODELS["groq"],
            })["model"],
            config.DEFAULT_MODELS["openai"],
        )
        self.assertEqual(
            config.normalize({
                "provider": "groq",
                "model": config.DEFAULT_MODELS["openai"],
            })["model"],
            config.DEFAULT_MODELS["groq"],
        )


class HistoryTests(unittest.TestCase):
    def test_add_trims_and_coerces_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.json"
            with mock.patch.object(history, "HISTORY_FILE", path):
                history.add("one", limit=2)
                entries = history.add(2, limit=2)

                self.assertEqual([e["text"] for e in entries], ["2", "one"])
                self.assertEqual(len(history.add("three", limit=2)), 2)

    def test_corrupt_history_loads_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.json"
            path.write_text("[", encoding="utf-8")
            with mock.patch.object(history, "HISTORY_FILE", path):
                self.assertEqual(history.load(), [])


class TranscriberTests(unittest.TestCase):
    def test_retries_transient_errors_and_rewinds_audio(self):
        calls = []
        wav = io.BytesIO(b"audio")

        class Response:
            def __init__(self, ok, status_code, text, payload=None):
                self.ok = ok
                self.status_code = status_code
                self.text = text
                self._payload = payload or {}

            def json(self):
                return self._payload

        def fake_post(url, headers, files, data, timeout):
            file_obj = files["file"][1]
            calls.append(file_obj.read())
            if len(calls) == 1:
                return Response(False, 503, "try again")
            return Response(True, 200, "", {"text": "hello"})

        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "key"}, clear=True):
            with mock.patch.object(transcriber.requests, "post", side_effect=fake_post):
                text = transcriber.transcribe(wav, "groq", "whisper-large-v3-turbo")

        self.assertEqual(text, "hello")
        self.assertEqual(calls, [b"audio", b"audio"])


class RecorderGateTests(unittest.TestCase):
    def test_silence_is_rejected(self):
        audio = np.zeros((16000, 1), dtype=np.int16)
        self.assertFalse(_has_speech(_speech_metrics(audio, 16000)))

    def test_short_active_speech_is_accepted(self):
        audio = np.zeros((16000, 1), dtype=np.int16)
        audio[2000:5000] = 500
        self.assertTrue(_has_speech(_speech_metrics(audio, 16000)))


if __name__ == "__main__":
    unittest.main()
