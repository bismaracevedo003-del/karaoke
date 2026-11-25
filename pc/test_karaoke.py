from __future__ import annotations

import time
import unittest

import numpy as np

from audio_engine import KaraokeEngine, resample_signal
from fake_mic import to_pcm16
from protocol import FRAME_SAMPLES, MAGIC, SAMPLE_RATE, make_packet, parse_packet


class ProtocolTests(unittest.TestCase):
    def test_roundtrip(self):
        packet = make_packet(7, b"\x01\x00\x02\x00", 48000)
        self.assertEqual(parse_packet(packet), (7, 48000, b"\x01\x00\x02\x00"))

    def test_rejects_bad_packets(self):
        self.assertIsNone(parse_packet(b""))
        self.assertIsNone(parse_packet(b"XXXX" + b"\x00" * 12))
        self.assertIsNone(parse_packet(make_packet(1, b"\x01", 48000)))
        bad_rate = MAGIC + b"\x01\x00\x00\x00\x01\x00\x00\x00" + b"\x00\x00"
        self.assertIsNone(parse_packet(bad_rate))


class EngineTests(unittest.TestCase):
    def test_resample_length(self):
        data = np.sin(np.arange(44100) / 44100 * 2 * np.pi * 440).astype(np.float32)
        self.assertEqual(len(resample_signal(data, 44100, 48000)), 48000)

    def test_mixes_music_and_mic(self):
        engine = KaraokeEngine(buffer_frames=1)
        engine.music_volume = 1.0
        engine.mic_volume = 1.0
        engine.gate = 0.0
        engine._track = np.full((FRAME_SAMPLES, 2), 0.5, dtype=np.float32)
        engine._playing = True
        engine._frames = {0: np.full(FRAME_SAMPLES, 0.25, dtype=np.float32)}
        engine._last_packet = time.monotonic()
        out = np.zeros((FRAME_SAMPLES, 2), dtype=np.float32)
        engine._audio_callback(out, FRAME_SAMPLES, None, None)
        np.testing.assert_allclose(out, 0.75, atol=1e-6)

    def test_mixes_at_44100_output(self):
        engine = KaraokeEngine(buffer_frames=1)
        engine._out_rate = 44100
        engine.music_volume = 1.0
        engine.mic_volume = 1.0
        engine.gate = 0.0
        engine._track = np.full((FRAME_SAMPLES, 2), 0.5, dtype=np.float32)
        engine._playing = True
        engine._frames = {0: np.full(FRAME_SAMPLES, 0.25, dtype=np.float32)}
        engine._last_packet = time.monotonic()
        out = np.zeros((882, 2), dtype=np.float32)
        engine._audio_callback(out, 882, None, None)
        np.testing.assert_allclose(out, 0.75, atol=1e-6)

    def test_silence_without_mic(self):
        engine = KaraokeEngine(buffer_frames=1)
        engine.music_volume = 1.0
        engine.mic_volume = 1.0
        engine._track = np.full((FRAME_SAMPLES, 2), 0.4, dtype=np.float32)
        engine._playing = True
        out = np.zeros((FRAME_SAMPLES, 2), dtype=np.float32)
        engine._audio_callback(out, FRAME_SAMPLES, None, None)
        np.testing.assert_allclose(out, 0.4, atol=1e-6)
        self.assertEqual(engine.stats()["lost"], 0)

    def test_live_source_mixes(self):
        engine = KaraokeEngine(buffer_frames=1)
        engine.music_volume = 1.0
        engine.mic_volume = 0.0
        engine.gate = 0.0
        engine._live_active = True
        engine._playing = True
        engine._live_buffer.append(np.full((FRAME_SAMPLES, 2), 0.6, dtype=np.float32))
        out = np.zeros((FRAME_SAMPLES, 2), dtype=np.float32)
        engine._audio_callback(out, FRAME_SAMPLES, None, None)
        np.testing.assert_allclose(out, 0.6, atol=1e-6)

    def test_live_source_paused_is_silent(self):
        engine = KaraokeEngine(buffer_frames=1)
        engine.music_volume = 1.0
        engine.gate = 0.0
        engine._live_active = True
        engine._playing = False
        engine._live_buffer.append(np.full((FRAME_SAMPLES, 2), 0.6, dtype=np.float32))
        out = np.full((FRAME_SAMPLES, 2), 1.0, dtype=np.float32)
        engine._audio_callback(out, FRAME_SAMPLES, None, None)
        np.testing.assert_allclose(out, 0.0, atol=1e-6)

    def test_play_without_track_in_live_mode(self):
        engine = KaraokeEngine(buffer_frames=1)
        engine._live_active = True
        engine.play()
        self.assertTrue(engine.playing)
        engine.pause()
        self.assertFalse(engine.playing)

    def test_missing_packet_is_counted(self):
        engine = KaraokeEngine(buffer_frames=1)
        engine._frames = {5: np.zeros(FRAME_SAMPLES, dtype=np.float32)}
        engine._last_packet = time.monotonic()
        out = np.zeros((FRAME_SAMPLES, 2), dtype=np.float32)
        engine._audio_callback(out, FRAME_SAMPLES, None, None)
        engine._audio_callback(out, FRAME_SAMPLES, None, None)
        self.assertEqual(engine.stats()["lost"], 1)

    def test_duplicate_packet_ignored(self):
        engine = KaraokeEngine(buffer_frames=1)
        pcm = np.full(FRAME_SAMPLES, 1000, dtype="<i2").tobytes()
        packet = make_packet(0, pcm, SAMPLE_RATE)
        addr = ("127.0.0.1", 40000)
        self.assertTrue(engine._ingest(packet, addr))
        self.assertFalse(engine._ingest(packet, addr))
        self.assertEqual(engine.stats()["received"], 1)
        self.assertEqual(len(engine._frames), 1)

    def test_startup_keeps_frames_for_buffer(self):
        engine = KaraokeEngine(buffer_frames=8)
        pcm = np.full(FRAME_SAMPLES, 1000, dtype="<i2").tobytes()
        for seq in range(10):
            engine._ingest(make_packet(seq, pcm, SAMPLE_RATE), ("127.0.0.1", 40000))
        self.assertEqual(len(engine._frames), 10)
        self.assertEqual(engine.stats()["received"], 10)

    def test_new_session_resets_sequence(self):
        engine = KaraokeEngine(buffer_frames=1)
        pcm = np.full(FRAME_SAMPLES, 1000, dtype="<i2").tobytes()
        engine._ingest(make_packet(500, pcm, SAMPLE_RATE), ("127.0.0.1", 40000))
        self.assertTrue(engine._ingest(make_packet(0, pcm, SAMPLE_RATE), ("127.0.0.1", 40001)))
        self.assertEqual(engine.stats()["received"], 1)
        self.assertIn(0, engine._frames)

    def test_conceals_lost_packet(self):
        engine = KaraokeEngine(buffer_frames=1)
        engine.music_volume = 0.0
        engine.mic_volume = 1.0
        engine.gate = 0.0
        pcm = np.full(FRAME_SAMPLES, 2000, dtype="<i2").tobytes()
        engine._ingest(make_packet(0, pcm, SAMPLE_RATE), ("127.0.0.1", 40000))
        out = np.zeros((FRAME_SAMPLES, 2), dtype=np.float32)
        engine._audio_callback(out, FRAME_SAMPLES, None, None)
        self.assertGreater(float(np.abs(out).max()), 0.0)
        out.fill(0.0)
        engine._audio_callback(out, FRAME_SAMPLES, None, None)
        self.assertGreater(float(np.abs(out).max()), 0.0)
        self.assertEqual(engine.stats()["lost"], 1)
        self.assertLess(float(engine.stats()["quality"]), 100.0)


class FakeMicTests(unittest.TestCase):
    def test_to_pcm16_scales_float_audio(self):
        samples = np.array([0.0, 0.5, -1.0, 1.0], dtype=np.float32)
        np.testing.assert_array_equal(
            to_pcm16(samples),
            np.array([0, 16383, -32767, 32767], dtype="<i2"),
        )


if __name__ == "__main__":
    unittest.main()
