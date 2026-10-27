import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config, recorder
from src.audio_environment import AudioEnvironment, BufferList
from src.recorder import Recorder


class InputSelectionTests(unittest.TestCase):
    def test_old_configs_follow_mac_default_and_explicit_choice_survives_restart(self):
        self.assertIsNone(config.normalize({}).get("input_device_uid"))
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(config, "CONFIG_FILE", Path(directory) / "config.json"):
                config.save({"input_device_uid": "phone-uid", "input_device_name": "My iPhone"})
                self.assertEqual(config.load()["input_device_uid"], "phone-uid")
                self.assertEqual(config.load()["input_device_name"], "My iPhone")
        for invalid in (42, [], {}, ""):
            self.assertIsNone(config.normalize({"input_device_uid": invalid})["input_device_uid"])

    def environment(self):
        environment = object.__new__(AudioEnvironment)
        environment.input_devices = mock.Mock(return_value=[
            {"id": 77, "uid": "mac", "name": "Mac mic", "channels": 1},
            {"id": 82, "uid": "phone", "name": "Phone mic", "channels": 1},
        ])
        environment.value = mock.Mock(return_value=77)
        return environment

    def test_default_is_resolved_live_but_explicit_selection_ignores_default(self):
        env = self.environment()
        self.assertEqual(env.resolve_input(None)["id"], 77)
        env.value.return_value = 82
        self.assertEqual(env.resolve_input(None)["id"], 82)
        self.assertEqual(env.resolve_input("mac")["id"], 77)

    def test_reconnected_device_uses_uid_not_old_numeric_id_or_name(self):
        env = self.environment()
        env.input_devices.return_value = [
            {"id": 99, "uid": "phone", "name": "Renamed phone", "channels": 1}]
        self.assertEqual(env.resolve_input("phone")["id"], 99)

    def test_missing_selected_mic_does_not_fall_back_to_another_mic(self):
        env = self.environment()
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            env.resolve_input("disconnected-phone")

    def test_enumeration_excludes_outputs_and_devices_disappearing_during_read(self):
        env = object.__new__(AudioEnvironment)
        env.ids = mock.Mock(return_value=(77, 70, 82))
        env.input_channels = mock.Mock(side_effect=(1, 0, OSError("removed")))
        env.string = mock.Mock(side_effect=lambda obj, prop: {"uid ": "mac", "lnam": "Mac mic"}[prop])
        self.assertEqual(env.input_devices(), [
            {"id": 77, "uid": "mac", "name": "Mac mic", "channels": 1}])

    def test_no_input_buffers_means_zero_channels(self):
        env = object.__new__(AudioEnvironment)
        env.data = mock.Mock(return_value=bytes(BufferList.buffers.offset))
        self.assertEqual(env.input_channels(70), 0)


class RecorderInputTests(unittest.TestCase):
    def test_explicit_device_is_passed_to_portaudio(self):
        capture = Recorder()
        with mock.patch.object(recorder.sd, "InputStream") as factory:
            capture._make_stream(16000, device=4)
        self.assertEqual(factory.call_args.kwargs["device"], 4)

    def test_rate_retry_and_duplicate_names_stay_on_requested_hardware(self):
        from src import input_devices
        target = {"id": 82, "uid": "phone", "name": "Same name", "channels": 1}
        first = mock.Mock(stopped=True, samplerate=16000)
        second = mock.Mock(stopped=True, samplerate=48000)
        capture = Recorder("phone")
        environment = mock.Mock()
        environment.resolve_input.return_value = target
        # The two devices share a name; the native stream ID determines identity.
        with mock.patch.object(input_devices, "AudioEnvironment", return_value=environment), \
             mock.patch.object(input_devices, "refresh_portaudio"), \
             mock.patch.object(input_devices, "candidate_indices", return_value=[0, 4]), \
             mock.patch.object(input_devices, "stream_device_id", side_effect=[77, 82]), \
             mock.patch.object(capture, "_make_stream", side_effect=[first, OSError("rate"), second]) as make, \
             self.assertLogs("speakrflow", level="ERROR"):
            stream, rate = capture._start_stream()
        self.assertIs(stream, second)
        environment.resolve_input.assert_called_once_with("phone")
        self.assertEqual(rate, 48000)
        self.assertEqual(make.call_args_list, [mock.call(16000, device=0),
                                              mock.call(16000, device=4),
                                              mock.call(None, device=4)])
        first.start.assert_not_called()
        first.close.assert_called_once()
        second.start.assert_called_once()

    def test_failed_start_keeps_route_leased_if_raw_stream_cannot_stop(self):
        from src import input_devices
        capture = Recorder()
        capture._call_capture = mock.Mock()
        capture._call_capture.acquire.return_value = None
        stream = mock.Mock(stopped=False, samplerate=16000)
        stream.start.side_effect = RuntimeError("start failed after opening IO")
        environment = mock.Mock()
        environment.resolve_input.return_value = {"id": 77, "name": "Mac mic"}
        with mock.patch.object(input_devices, "AudioEnvironment", return_value=environment), \
             mock.patch.object(input_devices, "refresh_portaudio"), \
             mock.patch.object(input_devices, "candidate_indices", return_value=[1]), \
             mock.patch.object(input_devices, "stream_device_id", return_value=77), \
             mock.patch.object(recorder.sd, "InputStream", return_value=stream), \
             mock.patch.object(recorder, "STOP_GRACE_SEC", .01), \
             self.assertLogs("speakrflow", level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "restart SpeakrFlow"):
                capture._start_stream()
        self.assertIs(capture._blocked_stream, stream)
        capture._call_capture.release.assert_not_called()


class SettingsApplyTests(unittest.TestCase):
    def test_changing_only_microphone_preserves_the_held_hotkey_listener(self):
        from src.app import SpeakrFlowApp
        app = object.__new__(SpeakrFlowApp)
        app.cfg = config.normalize({})
        app.recorder = Recorder()
        with mock.patch.object(app, "_start_hotkey") as restart, \
             mock.patch.object(app, "_rebuild_menu"):
            app._on_settings_saved({**app.cfg, "input_device_uid": "phone"})
        self.assertEqual(app.recorder.input_device_uid, "phone")
        restart.assert_not_called()


if __name__ == "__main__":
    unittest.main()
