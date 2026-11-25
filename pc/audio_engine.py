from __future__ import annotations

import csv
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd
import soundfile as sf

from protocol import DEFAULT_PORT, FRAME_SAMPLES, SAMPLE_RATE, make_ack, parse_packet

_RESAMPLE_CACHE: dict[tuple[int, int, int], tuple[np.ndarray, np.ndarray]] = {}
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
SVV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "herramientas",
    "SoundVolumeView.exe",
)
CABLE_FRIENDLY = "VB-Audio Virtual Cable\\Device\\CABLE Input\\Render"
SPEAKER_FALLBACK = "Realtek(R) Audio\\Device\\Speaker\\Render"


def _run_svv(*args: str) -> None:
    if not os.path.isfile(SVV_PATH):
        return
    try:
        subprocess.run(
            [SVV_PATH, *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def current_default_render() -> str:
    path = os.path.join(tempfile.gettempdir(), "karaoke_default_device.csv")
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
    _run_svv("/scomma", path, "/Columns", "Name,Type,Direction,Default,Command-Line Friendly ID")
    for _ in range(12):
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    if (
                        row.get("Direction") == "Render"
                        and row.get("Type") == "Device"
                        and row.get("Default") == "Render"
                    ):
                        return row.get("Command-Line Friendly ID", "")
        except (OSError, csv.Error):
            pass
        time.sleep(0.1)
    return ""


def set_default_render(friendly_id: str) -> None:
    if friendly_id:
        _run_svv("/SetDefault", friendly_id, "all")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(updates: dict) -> None:
    data = load_config()
    data.update(updates)
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def _host_name(index: int) -> str:
    try:
        return sd.query_hostapis(sd.query_devices(index)["hostapi"])["name"]
    except Exception:
        return ""


def _clean_device_name(raw: str) -> str:
    name = " ".join(raw.split())
    if "bthhfenum" in name.lower():
        match = re.search(r";\((.*)\)\s*$", name)
        if match:
            inner = match.group(1)
            if inner.endswith(")"):
                inner = inner[:-1]
            inner = inner.strip()
            if inner:
                return inner
    if name.endswith("()"):
        return ""
    return name


def match_device(name: str, devices: list[tuple[int, str]]) -> int | None:
    if not name:
        return None
    needle = name.lower()
    matches = [(index, dev_name) for index, dev_name in devices if needle in dev_name.lower()]
    if not matches:
        return None
    for index, _ in matches:
        if "wasapi" in _host_name(index).lower():
            return index
    return matches[0][0]


def _resample_indices(src_rate: int, dst_rate: int, n_out: int) -> tuple[np.ndarray, np.ndarray]:
    key = (src_rate, dst_rate, n_out)
    if key not in _RESAMPLE_CACHE:
        pos = np.arange(n_out, dtype=np.float64) * (src_rate / dst_rate)
        idx = np.floor(pos).astype(np.int64)
        _RESAMPLE_CACHE[key] = idx, (pos - idx).astype(np.float32)
    return _RESAMPLE_CACHE[key]


def resample_signal(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return data
    channels = data.shape[1] if data.ndim > 1 else 1
    if len(data) == 0:
        return np.zeros((0, channels), dtype=np.float32)
    n_out = int(round(len(data) * dst_rate / src_rate))
    flat = data.reshape(len(data), channels)
    idx, frac = _resample_indices(src_rate, dst_rate, n_out)
    i0 = np.minimum(idx, len(flat) - 1)
    i1 = np.minimum(i0 + 1, len(flat) - 1)
    out = flat[i0] * (1.0 - frac[:, None]) + flat[i1] * frac[:, None]
    return out.astype(np.float32)


class KaraokeEngine:
    def __init__(self, port: int = DEFAULT_PORT, buffer_frames: int = 8):
        self.port = port
        self.buffer_frames = max(1, buffer_frames)
        self.music_volume = 0.85
        self.mic_volume = 1.5
        self.gate = 0.012
        self._track: np.ndarray | None = None
        self._playhead = 0
        self._playing = False
        self._config = load_config()
        self._stream: sd.OutputStream | None = None
        self._out_rate = SAMPLE_RATE
        self._out_channels = 2
        self._output_device = match_device(str(self._config.get("output_device", "")), self.output_devices())
        self._sock: socket.socket | None = None
        self._receiver: threading.Thread | None = None
        self._status_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._frames: dict[int, np.ndarray] = {}
        self._next_seq: int | None = None
        self._mic_started = False
        self._last_packet = 0.0
        self._peer_addr: tuple[str, int] | None = None
        self._last_mic: np.ndarray | None = None
        self._conceal_count = 0
        self._mic_pending = np.zeros(0, dtype=np.float32)
        self._live_active = False
        self._live_stream: sd.InputStream | None = None
        self._live_buffer: deque[np.ndarray] = deque(maxlen=8)
        self._live_lock = threading.Lock()
        self._lock = threading.Lock()
        self.packets_received = 0
        self.packets_lost = 0
        self.last_error = ""

    @staticmethod
    def local_ip() -> str:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            probe.close()

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def duration(self) -> float:
        return len(self._track) / SAMPLE_RATE if self._track is not None else 0.0

    @property
    def position(self) -> float:
        return self._playhead / SAMPLE_RATE

    @property
    def progress(self) -> float:
        return min(1.0, self.position / self.duration) if self.duration else 0.0

    @property
    def mic_connected(self) -> bool:
        return time.monotonic() - self._last_packet < 1.0

    @property
    def live_active(self) -> bool:
        return self._live_active

    @staticmethod
    def _devices(flow: str) -> list[tuple[int, str]]:
        candidates: list[tuple[int, str, str, bool]] = []
        for index, info in enumerate(sd.query_devices()):
            if flow == "in" and info["max_input_channels"] <= 0:
                continue
            if flow == "out" and info["max_output_channels"] <= 0:
                continue
            raw = info["name"]
            lowered = raw.lower()
            if flow == "out" and "bthhfenum" in lowered and info["max_output_channels"] < 2:
                continue
            if flow == "out" and ("cable input" in lowered or "cable in" in lowered):
                continue
            if any(
                token in lowered
                for token in (
                    "sound mapper",
                    "asignador de sonido",
                    "primary sound",
                    "controlador primario",
                )
            ):
                continue
            name = _clean_device_name(raw)
            if not name:
                continue
            try:
                host = sd.query_hostapis(info["hostapi"])["name"].lower()
            except Exception:
                host = ""
            candidates.append((index, name, host, "bthhfenum" in lowered))

        wasapi_devices = [(index, name) for index, name, host, _ in candidates if "wasapi" in host]
        seen = {name.lower() for _, name in wasapi_devices}
        extras: list[tuple[int, str]] = []
        for index, name, host, bluetooth in candidates:
            if "wasapi" in host or not bluetooth:
                continue
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            extras.append((index, name))
        return wasapi_devices + extras

    @staticmethod
    def has_cable() -> bool:
        return any("cable input" in info["name"].lower() for info in sd.query_devices())

    @staticmethod
    def input_devices() -> list[tuple[int, str]]:
        return KaraokeEngine._devices("in")

    @staticmethod
    def output_devices() -> list[tuple[int, str]]:
        return KaraokeEngine._devices("out")

    def set_output_device(self, device: int | None) -> None:
        self._output_device = device
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._sock is not None:
            self._open_stream(device)

    @staticmethod
    def _pick_rate(device: int | None, channels: int = 2) -> int:
        candidates = [SAMPLE_RATE]
        try:
            info = sd.query_devices(device) if device is not None else sd.query_devices(kind="output")
            default = int(info["default_samplerate"])
            if default not in candidates:
                candidates.append(default)
        except Exception:
            pass
        for rate in candidates:
            try:
                sd.check_output_settings(device=device, samplerate=rate, channels=channels, dtype="float32")
                return rate
            except Exception:
                continue
        return candidates[-1]

    @staticmethod
    def _pick_channels(device: int | None) -> int:
        try:
            info = sd.query_devices(device) if device is not None else sd.query_devices(kind="output")
            return 1 if int(info["max_output_channels"]) < 2 else 2
        except Exception:
            return 2

    def _open_stream(self, device: int | None) -> None:
        try:
            channels = self._pick_channels(device)
            self._out_channels = channels
            self._out_rate = self._pick_rate(device, channels)
            self._mic_pending = np.zeros(0, dtype=np.float32)
            blocksize = max(1, int(round(FRAME_SAMPLES * self._out_rate / SAMPLE_RATE)))
            self._stream = sd.OutputStream(
                device=device,
                samplerate=self._out_rate,
                channels=channels,
                dtype="float32",
                blocksize=blocksize,
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as exc:
            self.last_error = str(exc)
            self.close()
            raise

    def start_live_source(self, device: int | None) -> None:
        self.stop_live_source()
        try:
            self._live_stream = sd.InputStream(
                device=device,
                samplerate=SAMPLE_RATE,
                channels=2,
                dtype="float32",
                blocksize=FRAME_SAMPLES,
                callback=self._live_callback,
            )
            self._live_stream.start()
        except Exception as exc:
            self.last_error = str(exc)
            self.stop_live_source()
            raise
        self._live_active = True

    def stop_live_source(self) -> None:
        self._live_active = False
        if self._live_stream is not None:
            try:
                self._live_stream.stop()
                self._live_stream.close()
            except Exception:
                pass
            self._live_stream = None
        with self._live_lock:
            self._live_buffer.clear()

    def _live_callback(self, indata, frames, time_info, status) -> None:
        block = np.asarray(indata, dtype=np.float32).copy()
        if block.ndim == 1:
            block = np.repeat(block[:, None], 2, axis=1)
        with self._live_lock:
            self._live_buffer.append(block)

    def _take_live_frame(self, frames: int) -> np.ndarray | None:
        with self._live_lock:
            if not self._live_buffer:
                return None
            block = self._live_buffer.popleft()
        if self._out_rate != SAMPLE_RATE:
            block = resample_signal(block, SAMPLE_RATE, self._out_rate)
        if block.shape[0] >= frames:
            return block[:frames]
        return np.pad(block, ((0, frames - block.shape[0]), (0, 0)))

    def load_track(self, path: str) -> None:
        data, rate = sf.read(path, dtype="float32", always_2d=True)
        if data.shape[1] > 2:
            data = data[:, :2]
        elif data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        data = resample_signal(data, int(rate), SAMPLE_RATE)
        self._track = np.ascontiguousarray(data, dtype=np.float32)
        self._playhead = 0
        self._playing = False

    def play(self) -> None:
        if not self._playing and (self._track is not None or self._live_active):
            self._playing = True

    def pause(self) -> None:
        self._playing = False

    def stop(self) -> None:
        self._playing = False
        self._playhead = 0

    def seek(self, fraction: float) -> None:
        if self._track is not None:
            self._playhead = int(min(1.0, max(0.0, fraction)) * len(self._track))

    def stats(self) -> dict[str, object]:
        with self._lock:
            total = self.packets_received + self.packets_lost
            quality = (100.0 * self.packets_received / total) if total else 100.0
            return {
                "received": self.packets_received,
                "lost": self.packets_lost,
                "buffered": len(self._frames),
                "connected": self.mic_connected,
                "quality": round(quality, 1),
            }

    def start(self) -> None:
        if self._stream is not None:
            return
        self._stop_event.clear()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        except OSError:
            pass
        try:
            self._sock.bind(("0.0.0.0", self.port))
        except OSError:
            self._sock.close()
            self._sock = None
            raise
        self._sock.settimeout(0.2)
        self._receiver = threading.Thread(target=self._receive_loop, daemon=True)
        self._receiver.start()
        self._status_thread = threading.Thread(target=self._status_loop, daemon=True)
        self._status_thread.start()
        self._open_stream(self._output_device)

    def close(self) -> None:
        self.stop_live_source()
        self._stop_event.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._receiver is not None:
            self._receiver.join(timeout=1)
            self._receiver = None
        if self._status_thread is not None:
            self._status_thread.join(timeout=1)
            self._status_thread = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _receive_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                time.sleep(0.05)
                continue
            self._ingest(data, addr)

    def _reset_mic_locked(self, reset_stats: bool = False) -> None:
        self._frames.clear()
        self._next_seq = None
        self._mic_started = False
        self._last_mic = None
        self._conceal_count = 0
        self._mic_pending = np.zeros(0, dtype=np.float32)
        if reset_stats:
            self.packets_received = 0
            self.packets_lost = 0

    def _ingest(self, data: bytes, addr: tuple[str, int] | None = None) -> bool:
        parsed = parse_packet(data)
        if parsed is None:
            return False
        seq, rate, pcm = parsed
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        if rate != SAMPLE_RATE:
            samples = resample_signal(samples.reshape(-1, 1), rate, SAMPLE_RATE).reshape(-1)
            samples = samples[:FRAME_SAMPLES]
            if len(samples) < FRAME_SAMPLES:
                samples = np.pad(samples, (0, FRAME_SAMPLES - len(samples)))
        with self._lock:
            if addr is not None:
                if self._peer_addr is not None and addr[1] != self._peer_addr[1]:
                    self._reset_mic_locked(reset_stats=True)
                self._peer_addr = addr
            if self._next_seq is not None and seq + 128 < self._next_seq:
                self._reset_mic_locked(reset_stats=True)
            if self._next_seq is not None and seq < self._next_seq:
                return False
            if seq in self._frames:
                return False
            self._frames[seq] = samples
            self.packets_received += 1
            self._last_packet = time.monotonic()
            if self._next_seq is not None:
                cutoff = self._next_seq - 4
                for old in [s for s in self._frames if s < cutoff]:
                    del self._frames[old]
            while len(self._frames) > 64:
                del self._frames[min(self._frames)]
        return True

    def _status_loop(self) -> None:
        while not self._stop_event.wait(0.5):
            sock = self._sock
            if sock is None:
                continue
            with self._lock:
                peer = self._peer_addr
                received = self.packets_received
                lost = self.packets_lost
            if peer is None:
                continue
            try:
                sock.sendto(make_ack(received, lost), peer)
            except OSError:
                pass

    def _consume_mic_packet_locked(self) -> np.ndarray | None:
        seq = self._next_seq or 0
        frame = self._frames.pop(seq, None)
        self._next_seq = seq + 1
        if frame is None:
            self.packets_lost += 1
            if self._last_mic is not None and self._conceal_count < 3:
                self._conceal_count += 1
                return self._last_mic * (1.0 - 0.25 * self._conceal_count)
            return np.zeros(FRAME_SAMPLES, dtype=np.float32)
        self._last_mic = frame.copy()
        self._conceal_count = 0
        return frame

    def _take_mic_frame(self, frames: int) -> np.ndarray:
        with self._lock:
            if self._mic_started and not self.mic_connected:
                self._reset_mic_locked()
            if not self._mic_started:
                if len(self._frames) < self.buffer_frames:
                    return np.zeros(frames, dtype=np.float32)
                self._next_seq = min(self._frames)
                self._mic_started = True
            while self._mic_pending.size < frames:
                frame = self._consume_mic_packet_locked()
                if frame is None:
                    break
                if self._out_rate != SAMPLE_RATE:
                    frame = resample_signal(
                        frame.reshape(-1, 1), SAMPLE_RATE, self._out_rate
                    ).reshape(-1)
                self._mic_pending = np.concatenate([self._mic_pending, frame])
            if self._mic_pending.size >= frames:
                out = self._mic_pending[:frames].copy()
                self._mic_pending = self._mic_pending[frames:]
                return out
            out = np.zeros(frames, dtype=np.float32)
            out[: self._mic_pending.size] = self._mic_pending
            self._mic_pending = np.zeros(0, dtype=np.float32)
            return out

    def _audio_callback(self, outdata, frames, time_info, status) -> None:
        music = np.zeros((frames, 2), dtype=np.float32)
        if self._live_active:
            if self._playing:
                live = self._take_live_frame(frames)
                if live is not None:
                    music[:] = live
        elif self._playing and self._track is not None:
            source_frames = max(1, int(round(frames * SAMPLE_RATE / self._out_rate)))
            end = min(self._playhead + source_frames, len(self._track))
            chunk = self._track[self._playhead:end]
            if len(chunk) < source_frames:
                chunk = np.pad(chunk, ((0, source_frames - len(chunk)), (0, 0)))
            if self._out_rate != SAMPLE_RATE:
                chunk = resample_signal(chunk, SAMPLE_RATE, self._out_rate)
            if chunk.shape[0] < frames:
                chunk = np.pad(chunk, ((0, frames - chunk.shape[0]), (0, 0)))
            music[:] = chunk[:frames]
            self._playhead = end
            if self._playhead >= len(self._track):
                self._playing = False
        mic = self._take_mic_frame(frames)
        level = float(np.sqrt(np.mean(mic * mic))) if mic.size else 0.0
        if level < self.gate:
            mic = mic * 0.05
        mixed = music * self.music_volume + mic[:, None] * self.mic_volume
        if self._out_channels == 1:
            outdata[:, 0] = np.clip(mixed.mean(axis=1), -1.0, 1.0)
        else:
            outdata[:] = np.clip(mixed, -1.0, 1.0)
