"""Synthetic regression tests; no audio endpoints, playback or model downloads."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from call_transcriber.audio import mono, read_mono
from call_transcriber import record
from call_transcriber.config import Config


class AudioTests(unittest.TestCase):
    def test_surround_downmix_preserves_stereo_level_and_input(self):
        stereo = np.full((1024, 2), .1, dtype=np.float32)
        surround = np.pad(stereo, ((0, 0), (0, 6)))
        original = surround.copy()
        np.testing.assert_allclose(mono(surround), mono(stereo))
        np.testing.assert_array_equal(surround, original)

    def test_mono_silent_empty_and_quiet_inputs(self):
        for block in (np.zeros((64, 8), dtype=np.float32),
                      np.zeros((0, 8), dtype=np.float32)):
            np.testing.assert_array_equal(mono(block), np.zeros(len(block)))
        quiet = np.full(64, 1e-8, dtype=np.float32)
        np.testing.assert_array_equal(mono(quiet), quiet)
        np.testing.assert_array_equal(mono(quiet[:, None]), quiet)

    def test_meter_and_native_flac_and_pcm(self):
        with tempfile.TemporaryDirectory() as session:
            track = record.Track('them', dict(defaultSampleRate=16000, maxInputChannels=8), session)
            stereo = np.full((8000, 2), .1, dtype=np.float32)
            audio = np.pad(stereo, ((0, 0), (0, 6)))
            try:
                track.t0 = 0
                track.received = 16000
                track.queue.put(audio)
                track.queue.put(np.zeros((8000, 8), dtype=np.float32))
                track.drain(1, last=True)
                self.assertAlmostEqual(track.level, -29.0309, places=3)
            finally:
                track.close()
            path = Path(session) / 'them.flac'
            before = path.read_bytes()
            raw, rate = sf.read(path, dtype='float32', always_2d=True)
            self.assertEqual(raw.shape, (16000, 8))
            self.assertEqual(rate, 16000)
            np.testing.assert_allclose(raw[:8000], audio, atol=2e-7)
            offline = read_mono(path, 16000)
            live = np.fromfile(Path(session) / 'them.pcm', dtype=np.float32)
            np.testing.assert_allclose(live, offline, atol=2e-7)
            self.assertEqual(before, path.read_bytes())

    def test_loopback_gap_padding(self):
        with tempfile.TemporaryDirectory() as session:
            track = record.Track('them', dict(defaultSampleRate=16000, maxInputChannels=2), session)
            try:
                track.t0 = 100
                track.pad_to(101, 0)
                track.pad_to(101, 0)
                self.assertEqual(track.received, 16000)
                self.assertEqual(track.queue.qsize(), 1)
                np.testing.assert_array_equal(track.queue.get(), np.zeros((16000, 2)))
            finally:
                track.close()

    def test_offline_resamples_native_surround(self):
        with tempfile.TemporaryDirectory() as session:
            path = Path(session) / 'synthetic.flac'
            audio = np.zeros((48000, 8), dtype=np.float32)
            audio[:, :2] = .1
            sf.write(path, audio, 48000, subtype='PCM_24')
            result = read_mono(path, 16000)
            self.assertEqual(result.shape, (16000,))
            np.testing.assert_allclose(result[100:-100], .1, atol=1e-5)

    def test_constructor_failure_closes_files(self):
        with tempfile.TemporaryDirectory() as session:
            with patch.object(record.soxr, 'ResampleStream', side_effect=RuntimeError('failure')):
                with self.assertRaisesRegex(RuntimeError, 'failure'):
                    record.Track('them', dict(defaultSampleRate=16000, maxInputChannels=2), session)
            for path in Path(session).iterdir():
                path.rename(path.with_suffix(path.suffix + '.closed'))


class DeviceTests(unittest.TestCase):
    def test_default_loopback_uses_windows_default(self):
        pa = Mock()
        with patch.object(record, 'wasapi_devices', return_value=[]):
            self.assertIs(record.find_device(pa, '', True), pa.get_default_wasapi_loopback.return_value)

    def test_matching_filters_endpoint_type_and_rejects_ambiguity(self):
        devices = [dict(name='Output A', maxInputChannels=2, isLoopbackDevice=True),
                   dict(name='Output B', maxInputChannels=2, isLoopbackDevice=True),
                   dict(name='Output microphone', maxInputChannels=1, isLoopbackDevice=False)]
        with patch.object(record, 'wasapi_devices', return_value=devices):
            self.assertIs(record.find_device(Mock(), 'output a', True), devices[0])
            with self.assertRaisesRegex(SystemExit, 'Ambiguous device'):
                record.find_device(Mock(), 'Output', True)
            with self.assertRaisesRegex(SystemExit, 'No device matching'):
                record.find_device(Mock(), 'Missing', True)

    def test_no_mic_and_failed_second_stream_cleanup(self):
        for no_mic in (True, False):
            with self.subTest(no_mic=no_mic), tempfile.TemporaryDirectory() as session:
                pa = Mock()
                stream = Mock()
                pa.open.side_effect = [OSError('unavailable')] if no_mic else [stream, OSError('unavailable')]
                device = dict(defaultSampleRate=16000, maxInputChannels=2, index=1, name='Synthetic endpoint')
                with patch.object(record.pyaudio, 'PyAudio', return_value=pa), \
                     patch.object(record.config_module, 'load', return_value=Config()), \
                     patch.object(record, 'find_device', return_value=device) as find:
                    with self.assertRaisesRegex(OSError, 'unavailable'):
                        record.main(['--session', session] + (['--no-mic'] if no_mic else []))
                self.assertEqual(find.call_count, 1 if no_mic else 2)
                pa.terminate.assert_called_once()
                if not no_mic:
                    stream.close.assert_called_once()
                self.assertFalse((Path(session) / 'RECORDED').exists())
                # Windows refuses this rename if a failed startup left FLAC open.
                for path in Path(session).glob('*.flac'):
                    path.rename(path.with_suffix('.closed'))


if __name__ == '__main__':
    unittest.main()
