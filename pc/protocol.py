from __future__ import annotations

import struct

MAGIC = b"KRM1"
HEADER = struct.Struct("<4sII")
SAMPLE_RATE = 48000
FRAME_SAMPLES = 960
FRAME_BYTES = FRAME_SAMPLES * 2
DEFAULT_PORT = 50007
MAX_RATE = 192000
MAX_PAYLOAD = 8192


ACK_MAGIC = b"KRM2"
ACK_HEADER = struct.Struct("<4sII")


def make_packet(seq: int, pcm: bytes, rate: int) -> bytes:
    return HEADER.pack(MAGIC, seq & 0xFFFFFFFF, int(rate)) + pcm


def make_ack(received: int, lost: int) -> bytes:
    return ACK_HEADER.pack(ACK_MAGIC, received & 0xFFFFFFFF, lost & 0xFFFFFFFF)


def parse_ack(data: bytes) -> tuple[int, int] | None:
    if len(data) != ACK_HEADER.size:
        return None
    magic, received, lost = ACK_HEADER.unpack(data)
    if magic != ACK_MAGIC:
        return None
    return received, lost


def parse_packet(data: bytes) -> tuple[int, int, bytes] | None:
    if len(data) < HEADER.size:
        return None
    magic, seq, rate = HEADER.unpack_from(data)
    if magic != MAGIC:
        return None
    if not 8000 <= rate <= MAX_RATE:
        return None
    pcm = data[HEADER.size:]
    if not pcm or len(pcm) > MAX_PAYLOAD or len(pcm) % 2:
        return None
    return seq, rate, pcm
