import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from src import certs, config, history, transcriber
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

        def fake_post(url, headers, files, data, timeout, verify):
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

    def _post_once(self, status_code, text):
        """Run transcribe() against a single canned error response."""
        class Response:
            ok = False

            def __init__(self):
                self.status_code = status_code
                self.text = text

            def json(self):
                return {}

        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "key"}, clear=True):
            with mock.patch.object(transcriber.requests, "post",
                                   side_effect=lambda *a, **k: Response()):
                with self.assertRaises(transcriber.TranscriptionError) as caught:
                    transcriber.transcribe(io.BytesIO(b"audio"), "groq", "m")
        return str(caught.exception)

    def test_network_block_403_explains_the_vpn(self):
        """Groq answers a healthy upload from a VPN exit IP with this 403.
        The raw body says 'check your network settings', which reads like a
        local misconfiguration and sends you debugging the wrong layer."""
        message = self._post_once(
            403, '{"error":{"message":"Access denied. Please check your network settings."}}')
        self.assertIn("VPN", message)
        self.assertIn("split tunnel", message.lower())

    def test_plain_403_is_not_mistaken_for_a_vpn_block(self):
        """A permissions/quota 403 must keep its own body, not blame the VPN."""
        message = self._post_once(
            403, '{"error":{"message":"You exceeded your current quota."}}')
        self.assertNotIn("VPN", message)
        self.assertIn("quota", message)

    def test_401_reports_a_bad_key(self):
        message = self._post_once(401, '{"error":{"message":"Invalid API Key"}}')
        self.assertIn("API key", message)


class HotkeyResilienceTests(unittest.TestCase):
    """macOS disables CGEventTaps after sleep/wake or throttling; the listener
    must re-enable its tap when the disabled notification arrives."""

    def _make_listener(self):
        from src import hotkey
        listener = hotkey.ResilientListener(
            on_press=lambda *a: None,
            on_release=lambda *a: None,
        )
        listener._tap = object()
        return hotkey, listener

    def test_reenables_tap_when_disabled_by_timeout(self):
        hotkey, listener = self._make_listener()
        with mock.patch.object(hotkey.Quartz, "CGEventTapEnable") as enable:
            listener._handle_message(
                None, hotkey.Quartz.kCGEventTapDisabledByTimeout, None, None, False)
        enable.assert_called_once_with(listener._tap, True)

    def test_reenables_tap_when_disabled_by_user_input(self):
        hotkey, listener = self._make_listener()
        with mock.patch.object(hotkey.Quartz, "CGEventTapEnable") as enable:
            listener._handle_message(
                None, hotkey.Quartz.kCGEventTapDisabledByUserInput, None, None, False)
        enable.assert_called_once_with(listener._tap, True)

    def test_normal_events_still_reach_pynput(self):
        hotkey, listener = self._make_listener()
        with mock.patch.object(
            hotkey.keyboard.Listener, "_handle_message"
        ) as base_handler:
            listener._handle_message(None, 10, "event", None, False)
        base_handler.assert_called_once_with(None, 10, "event", None, False)


class PasteTests(unittest.TestCase):
    """CGEventPost silently discards events without Accessibility permission;
    paste_text must fail loudly instead so the app can notify the user."""

    def test_raises_when_accessibility_not_granted(self):
        from src import paste
        with mock.patch.object(paste, "_copy_text"):
            with mock.patch.object(paste, "_accessibility_trusted", return_value=False):
                with mock.patch.object(paste, "_paste_with_quartz") as quartz:
                    with self.assertRaisesRegex(PermissionError, "Accessibility"):
                        paste.paste_text("hello")
        quartz.assert_not_called()

    def test_pastes_when_trusted(self):
        from src import paste
        with mock.patch.object(paste, "_copy_text") as copy:
            with mock.patch.object(paste, "_accessibility_trusted", return_value=True):
                with mock.patch.object(paste, "_paste_with_quartz") as quartz:
                    paste.paste_text("hello")
        copy.assert_called_once_with("hello")
        quartz.assert_called_once()


class StatusTests(unittest.TestCase):
    def test_transcribing_title_counts_seconds(self):
        from src import status
        self.assertEqual(status.transcribing_title(0.3), "…")
        self.assertEqual(status.transcribing_title(3.7), "… 3s")

    def test_status_lines(self):
        from src import status
        self.assertEqual(status.status_line("idle"), "Status: Idle")
        self.assertEqual(status.status_line("recording"), "Status: Recording…")
        self.assertEqual(status.status_line("transcribing"), "Status: Transcribing…")

    def test_last_line_formats_success_and_error(self):
        from src import status
        ok = status.last_line(True, "hello world", 1234.0)
        self.assertTrue(ok.startswith("Last: ✓"))
        err = status.last_line(False, "x" * 100, 1234.0)
        self.assertTrue(err.startswith("Last: ⚠"))
        self.assertLess(len(err), 90)


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


class CaBundleTests(unittest.TestCase):
    """py2app zips certifi into python3xx.zip, so certifi.where() extracts
    cacert.pem to a temp file under $TMPDIR and caches the path forever.
    macOS's dirhelper purges $TMPDIR after ~3 days, so a menu bar app left
    running over a quiet weekend loses its CA bundle mid-process."""

    def test_heals_when_certifi_path_was_purged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            purged = Path(tmpdir) / "T" / "tmpXXXXcacert.pem"
            bundle = Path(tmpdir) / "cacert.pem"

            with mock.patch.object(certs.certifi, "where", return_value=str(purged)):
                with mock.patch.object(certs, "_BUNDLE", bundle):
                    path = certs.ca_bundle()

            self.assertTrue(os.path.exists(path), "must return a path that exists")
            self.assertIn("BEGIN CERTIFICATE", Path(path).read_text(encoding="ascii"))

    def test_uses_certifi_directly_when_its_file_is_real(self):
        """The healthy case must not copy 236KB on every transcription."""
        with tempfile.TemporaryDirectory() as tmpdir:
            real = Path(tmpdir) / "cacert.pem"
            real.write_text("-----BEGIN CERTIFICATE-----", encoding="ascii")

            with mock.patch.object(certs.certifi, "where", return_value=str(real)):
                self.assertEqual(certs.ca_bundle(), str(real))

    def test_transcribe_verifies_against_a_bundle_that_exists(self):
        seen = {}

        class Response:
            ok = True
            status_code = 200
            text = ""

            def json(self):
                return {"text": "hello"}

        def fake_post(url, headers, files, data, timeout, verify):
            seen["verify"] = verify
            return Response()

        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "key"}, clear=True):
            with mock.patch.object(transcriber.requests, "post", side_effect=fake_post):
                transcriber.transcribe(io.BytesIO(b"audio"), "groq", "m")

        self.assertTrue(os.path.exists(seen["verify"]))

    def test_bundle_ships_certifi_unzipped(self):
        """The real fix: py2app must copy certifi as a real directory. If it
        falls back into the zip, where() resumes extracting to $TMPDIR and the
        3-day purge comes back."""
        source = Path("setup.py").read_text(encoding="utf-8")
        self.assertIn("certifi", source)
