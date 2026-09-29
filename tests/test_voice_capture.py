import threading
import unittest
from unittest import mock

from src.voice_capture import CallCapture, VoiceStream, needs_voice_capture


def state(channels=1, active=(), builtin=True, device=78):
    return dict(device=device, channels=channels, builtin=builtin,
                external_inputs=active, processes=active)


class CallCaptureTests(unittest.TestCase):
    def manager(self, initial):
        environment = mock.Mock()
        environment.snapshot.return_value = initial
        environment.dirty = threading.Event()
        factory = mock.Mock()
        capture = CallCapture(mock.Mock(), environment, factory)
        return capture, environment, factory

    def test_normal_input_and_external_multichannel_devices_keep_existing_capture(self):
        for value in (state(), state(3), state(3, (10,), builtin=False)):
            with self.subTest(value=value):
                capture, _, factory = self.manager(value)
                self.assertIsNone(capture.acquire())
                factory.assert_not_called()

    def test_call_mode_uses_processed_capture_and_prepares_without_recording(self):
        capture, _, factory = self.manager(state(3, (10,)))
        capture._refresh()
        factory.return_value.start.assert_not_called()
        self.assertIs(capture.acquire(), factory.return_value)
        factory.assert_called_once()
        self.assertTrue(capture.leased)

    def test_own_voice_unit_does_not_keep_call_mode_alive_after_call(self):
        capture, env, factory = self.manager(state(3, (10,)))
        capture._refresh()
        env.snapshot.return_value = state(3)  # Our warmed unit still contributes 3 channels.
        self.assertIsNone(capture.acquire())
        factory.return_value.dispose.assert_called_once()

    def test_rechecks_hardware_after_removing_stale_voice_unit(self):
        capture, env, factory = self.manager(state(3, (10,)))
        capture._refresh()
        # The call ended but a different, ordinary recorder still uses the mic.
        env.snapshot.side_effect = [state(3, (11,)), state(1, (11,))]
        self.assertIsNone(capture.acquire())
        factory.return_value.dispose.assert_called_once()
        factory.assert_called_once()

    def test_configuration_changes_wait_until_current_recording_finishes(self):
        capture, env, factory = self.manager(state(3, (10,)))
        stream = capture.acquire()
        env.snapshot.return_value = state()
        capture._refresh()
        stream.dispose.assert_not_called()
        capture.release()
        self.assertTrue(env.dirty.is_set())
        capture._refresh()
        stream.dispose.assert_called_once()

    def test_new_default_device_drops_prepared_old_input(self):
        capture, env, factory = self.manager(state(3, (10,)))
        capture._refresh()
        env.snapshot.return_value = state(2, (10,), builtin=False, device=90)
        self.assertIsNone(capture.acquire())
        factory.return_value.dispose.assert_called_once()

    def test_cannot_silently_revert_to_quiet_raw_input_when_voice_setup_fails(self):
        capture, _, factory = self.manager(state(3, (10,)))
        factory.side_effect = RuntimeError("native startup failed")
        with self.assertRaisesRegex(RuntimeError, "native startup failed"):
            capture.acquire()

    def test_float_samples_are_copied_and_clipped_before_callback_returns(self):
        import ctypes as C
        import numpy as np
        stream = object.__new__(VoiceStream)
        stream.callback = mock.Mock()
        samples = (C.c_float * 4)(-2, -.5, .5, 2)
        stream._received(samples, 4)
        result = stream.callback.call_args.args[0]
        samples[1] = 0
        np.testing.assert_array_equal(result[:, 0], [-32768, -16384, 16384, 32767])


if __name__ == "__main__":
    unittest.main()
