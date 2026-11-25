from __future__ import annotations

import socket
import threading
import time

import numpy as np

from audio_engine import KaraokeEngine
from protocol import FRAME_SAMPLES, SAMPLE_RATE, make_packet

PORT = 50123


def main() -> None:
    engine = KaraokeEngine(port=PORT)
    engine.music_volume = 0.0
    engine.mic_volume = 0.0
    engine.start()

    def send() -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for seq in range(120):
            pcm = (0.2 * np.sin(2 * np.pi * 440 * np.arange(FRAME_SAMPLES) / SAMPLE_RATE) * 32767).astype("<i2")
            sock.sendto(make_packet(seq, pcm.tobytes(), SAMPLE_RATE), ("127.0.0.1", PORT))
            time.sleep(FRAME_SAMPLES / SAMPLE_RATE)
        sock.close()

    sender = threading.Thread(target=send, daemon=True)
    sender.start()
    time.sleep(2.5)
    stats = engine.stats()
    engine.close()
    print(stats)
    assert int(stats["received"]) > 80, stats
    assert stats["connected"] is True, stats
    print("OK")


if __name__ == "__main__":
    main()
