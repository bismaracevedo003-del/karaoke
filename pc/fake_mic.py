from __future__ import annotations

import argparse
import socket
import time

import numpy as np

from protocol import DEFAULT_PORT, FRAME_SAMPLES, SAMPLE_RATE, make_packet


def to_pcm16(samples: np.ndarray) -> np.ndarray:
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")


def main() -> None:
    parser = argparse.ArgumentParser(description="Cliente de micro falso para probar el karaoke")
    parser.add_argument("--ip", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--file", default="")
    parser.add_argument("--freq", type=float, default=440.0)
    parser.add_argument("--seconds", type=float, default=0.0)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    source = None
    rate = SAMPLE_RATE
    if args.file:
        import soundfile as sf

        source, rate = sf.read(args.file, dtype="float32", always_2d=True)
        if source.shape[1] > 1:
            source = source.mean(axis=1)

    step = FRAME_SAMPLES / rate
    start = time.monotonic()
    seq = 0
    phase = 0.0
    cursor = 0
    try:
        while True:
            elapsed = time.monotonic() - start
            if args.seconds and elapsed >= args.seconds:
                break
            if source is not None:
                end = min(cursor + FRAME_SAMPLES, len(source))
                chunk = source[cursor:end]
                cursor = end
                if len(chunk) < FRAME_SAMPLES:
                    chunk = np.pad(chunk, (0, FRAME_SAMPLES - len(chunk)))
            else:
                positions = phase + np.arange(FRAME_SAMPLES) / rate
                chunk = 0.3 * np.sin(2 * np.pi * args.freq * positions)
                phase += step
            pcm = to_pcm16(chunk)
            sock.sendto(make_packet(seq, pcm.tobytes(), rate), (args.ip, args.port))
            seq += 1
            target = start + seq * step
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -step:
                start = time.monotonic() - seq * step
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
