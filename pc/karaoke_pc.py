from __future__ import annotations

import os
import re
import tempfile
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

try:
    import segno
except Exception:  # pragma: no cover - optional dependency
    segno = None

import sounddevice as sd

from audio_engine import (
    CABLE_FRIENDLY,
    SPEAKER_FALLBACK,
    KaraokeEngine,
    current_default_render,
    load_config,
    save_config,
    set_default_render,
)
from lan_server import LanPageServer
from protocol import DEFAULT_PORT


def parse_lrc(path: str) -> list[tuple[float, str]]:
    if not path:
        return []
    lines: list[tuple[float, str]] = []
    pattern = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:\.\d+)?)\](.*)")
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            for match in pattern.finditer(raw):
                text = match.group(3).strip()
                if text:
                    stamp = int(match.group(1)) * 60 + float(match.group(2))
                    lines.append((stamp, text))
    lines.sort()
    return lines


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.failed = False
        self._routing = False
        self._previous_default = ""
        self.engine = KaraokeEngine()
        self.server: LanPageServer | None = None
        self._qr_image = None
        self._ip_ticks = 0
        self.lyrics: list[tuple[float, str]] = []
        self._device_map: dict[str, int] = {}
        self._output_map: dict[str, int] = {}
        self.lyric_index = -2
        self.track_path = ""
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            self.engine.start()
        except Exception as exc:
            messagebox.showerror(
                "Audio",
                f"No se pudo iniciar el audio o el puerto {DEFAULT_PORT} ya está en uso:\n{exc}",
            )
            self.failed = True
            return
        apk_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "karaoke-mic.apk",
        )
        self.server = LanPageServer(DEFAULT_PORT, apk_path, self.engine.local_ip())
        self.server.start()
        if self.engine.has_cable():
            self._previous_default = current_default_render()
            set_default_render(CABLE_FRIENDLY)
            self._routing = True
        live_name = str(load_config().get("live_device", ""))
        if live_name and any(live_name.lower() in name.lower() for name in self._device_map):
            self.source_var.set("live")
            self._apply_source()
        self._tick()

    def _build(self) -> None:
        self.root.title("Karaoke PC")
        self.root.geometry("650x720")
        self.root.minsize(650, 720)

        main = ttk.Frame(self.root, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, text="KARAOKE PC", font=("Segoe UI", 18, "bold")).pack(anchor=tk.W)
        ttk.Label(main, text="Cargá una pista, conectá los parlantes y abrí la app del celular.").pack(anchor=tk.W, pady=(4, 14))

        track_frame = ttk.Frame(main)
        track_frame.pack(fill=tk.X, pady=4)
        self.track_var = tk.StringVar(value="Ninguna pista cargada")
        ttk.Label(track_frame, textvariable=self.track_var, width=52, relief="groove", padding=4).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(track_frame, text="Buscar...", command=self._browse).pack(side=tk.LEFT, padx=(8, 0))

        source_frame = ttk.LabelFrame(main, text="Fuente de música", padding=8)
        source_frame.pack(fill=tk.X, pady=(10, 0))
        self.source_var = tk.StringVar(value="archivo")
        ttk.Radiobutton(source_frame, text="Pista (archivo)", variable=self.source_var, value="archivo", command=self._apply_source).pack(side=tk.LEFT)
        ttk.Radiobutton(source_frame, text="Entrada en vivo", variable=self.source_var, value="live", command=self._apply_source).pack(side=tk.LEFT, padx=(12, 0))
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(source_frame, textvariable=self.device_var, state="readonly", width=30)
        self.device_combo.pack(side=tk.LEFT, padx=(12, 0))
        self.device_combo.bind("<Button-1>", lambda event: self._refresh_devices())
        ttk.Button(source_frame, text="Actualizar", command=self._refresh_devices, width=11).pack(side=tk.LEFT, padx=(6, 0))
        output_frame = ttk.Frame(main)
        output_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(output_frame, text="Salida:").pack(side=tk.LEFT)
        self.output_var = tk.StringVar()
        self.output_combo = ttk.Combobox(output_frame, textvariable=self.output_var, state="readonly", width=44)
        self.output_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.output_combo.bind("<Button-1>", lambda event: self._refresh_devices())
        ttk.Button(output_frame, text="Aplicar", command=self._apply_output, width=9).pack(side=tk.LEFT, padx=(6, 0))
        self._refresh_devices()

        control_frame = ttk.Frame(main)
        control_frame.pack(fill=tk.X, pady=12)
        self.play_button = ttk.Button(control_frame, text="Reproducir", command=self._toggle, width=14)
        self.play_button.pack(side=tk.LEFT)
        ttk.Button(control_frame, text="Detener", command=self._stop, width=12).pack(side=tk.LEFT, padx=8)
        self.time_var = tk.StringVar(value="00:00 / 00:00")
        ttk.Label(control_frame, textvariable=self.time_var).pack(side=tk.RIGHT)

        self.progress = ttk.Progressbar(main, mode="determinate", maximum=1000)
        self.progress.pack(fill=tk.X, pady=4)

        ttk.Label(main, text="Mezcla", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, pady=(12, 4))
        self.music_scale = tk.Scale(main, from_=0, to=150, orient=tk.HORIZONTAL, label="Música (%)", command=self._set_music)
        self.music_scale.set(int(self.engine.music_volume * 100))
        self.music_scale.pack(fill=tk.X)
        self.mic_scale = tk.Scale(main, from_=0, to=300, orient=tk.HORIZONTAL, label="Micro (%)", command=self._set_mic)
        self.mic_scale.set(int(self.engine.mic_volume * 100))
        self.mic_scale.pack(fill=tk.X)
        self.gate_scale = tk.Scale(main, from_=0, to=200, orient=tk.HORIZONTAL, label="Puerta de ruido", command=self._set_gate)
        self.gate_scale.set(int(self.engine.gate * 10000))
        self.gate_scale.pack(fill=tk.X)

        ttk.Label(main, text="Conexión del celular", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, pady=(12, 4))
        net = ttk.Frame(main)
        net.pack(fill=tk.X)
        ttk.Label(net, text="IP del PC:").pack(side=tk.LEFT)
        self.ip_var = tk.StringVar(value=self.engine.local_ip())
        ttk.Entry(net, textvariable=self.ip_var, state="readonly", width=20).pack(side=tk.LEFT, padx=(6, 12))
        ttk.Label(net, text="Puerto:").pack(side=tk.LEFT)
        ttk.Entry(net, textvariable=tk.StringVar(value=str(DEFAULT_PORT)), state="readonly", width=8).pack(side=tk.LEFT, padx=6)
        ttk.Button(net, text="Conectar celular (QR)", command=self._show_qr).pack(side=tk.LEFT, padx=(18, 0))

        self.status_var = tk.StringVar(value="Micro: esperando conexión")
        self.status_label = ttk.Label(main, textvariable=self.status_var, foreground="#a00")
        self.status_label.pack(anchor=tk.W, pady=(6, 2))
        self.stats_var = tk.StringVar(value="Paquetes: 0 | perdidos: 0 | buffer: 0")
        ttk.Label(main, textvariable=self.stats_var).pack(anchor=tk.W)

        ttk.Label(main, text="Letra (.lrc junto a la pista)", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, pady=(12, 4))
        self.lyric_text = tk.Text(main, height=6, wrap=tk.WORD, font=("Segoe UI", 11), state=tk.DISABLED)
        self.lyric_text.pack(fill=tk.BOTH, expand=True)

        help_text = (
            "1) PC y celular en la misma Wi-Fi.   "
            "2) Tocá «Conectar celular (QR)» y escaneá con la cámara.   "
            "3) YouTube → CABLE Input; entrada en vivo = CABLE Output; Salida = tus parlantes.   "
            "4) En modo en vivo, tocá «Reproducir» para empezar a escuchar."
        )
        ttk.Label(main, text=help_text, wraplength=610, foreground="#444").pack(anchor=tk.W, pady=(10, 0))

    def _refresh_devices(self) -> None:
        config = load_config()

        devices = self.engine.input_devices()
        self._device_map = {name: index for index, name in devices}
        names = list(self._device_map)
        self.device_combo["values"] = names
        if names and self.device_var.get() not in names:
            configured = str(config.get("live_device", ""))
            preferred = next((n for n in names if configured and configured.lower() in n.lower()), None)
            if preferred is None:
                preferred = next((n for n in names if "cable output" in n.lower()), names[0])
            self.device_var.set(preferred)

        outputs = self.engine.output_devices()
        self._output_map = {name: index for index, name in outputs}
        out_names = list(self._output_map)
        self.output_combo["values"] = out_names
        if out_names and self.output_var.get() not in out_names:
            configured = str(config.get("output_device", ""))
            preferred = next((n for n in out_names if configured and configured.lower() in n.lower()), None)
            if preferred is None:
                current = sd.default.device[1]
                preferred = next((n for n, i in self._output_map.items() if i == current), out_names[0])
            self.output_var.set(preferred)

    def _apply_output(self) -> None:
        name = self.output_var.get()
        index = self._output_map.get(name)
        if index is None:
            return
        try:
            self.engine.set_output_device(index)
            save_config({"output_device": name})
        except Exception as exc:
            messagebox.showerror("Audio", f"No se pudo abrir la salida:\n{exc}")

    def _show_qr(self) -> None:
        if self.server is None or not self.server.started:
            messagebox.showerror("QR", "No se pudo iniciar la página de conexión.\nProbá de nuevo o usá la IP manualmente.")
            return
        url = self.server.page_url
        win = tk.Toplevel(self.root)
        win.title("Conectar celular")
        win.geometry("430x600")
        win.resizable(False, False)
        win.transient(self.root)
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame,
            text="Escaneá el QR con la cámara del celular",
            font=("Segoe UI", 12, "bold"),
        ).pack()
        if segno is not None:
            try:
                path = os.path.join(tempfile.gettempdir(), "karaoke_qr.png")
                segno.make(url, error="m").save(path, scale=8, border=2)
                image = tk.PhotoImage(file=path)
                self._qr_image = image
                ttk.Label(frame, image=image).pack(pady=12)
            except Exception as exc:
                ttk.Label(frame, text=f"No se pudo dibujar el QR:\n{exc}", foreground="#a00", wraplength=380).pack(pady=12)
        else:
            ttk.Label(frame, text="(Falta el paquete 'segno' para mostrar el QR)", foreground="#a00").pack(pady=12)
        ttk.Label(
            frame,
            text="1) Instalá la app desde la página.\n2) Tocá «Abrir Karaoke Móvil» y se conecta sola.",
            justify="center",
        ).pack()
        url_var = tk.StringVar(value=url)
        ttk.Entry(frame, textvariable=url_var, state="readonly", width=48).pack(pady=(14, 8))
        row = ttk.Frame(frame)
        row.pack()
        ttk.Button(row, text="Copiar enlace", command=lambda: self._copy_link(url)).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="Abrir página", command=lambda: webbrowser.open(url)).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="Cerrar", command=win.destroy).pack(side=tk.LEFT, padx=4)
        ttk.Label(frame, text="PC y celular en la misma Wi-Fi.", foreground="#666").pack(pady=(14, 0))

    def _copy_link(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _apply_source(self) -> None:
        if self.source_var.get() == "live":
            name = self.device_var.get()
            index = self._device_map.get(name)
            if index is None:
                messagebox.showinfo("Karaoke", "No hay dispositivos de entrada disponibles.")
                self.source_var.set("archivo")
                return
            try:
                self.engine.start_live_source(index)
            except Exception as exc:
                messagebox.showerror("Audio", f"No se pudo abrir la entrada:\n{exc}")
                self.source_var.set("archivo")
                return
            save_config({"live_device": name})
            self.engine.pause()
            self.play_button.configure(text="Reproducir")
        else:
            self.engine.stop_live_source()
            self.engine.pause()
            self.play_button.configure(text="Reproducir")

    def _set_music(self, value: str) -> None:
        self.engine.music_volume = int(value) / 100

    def _set_mic(self, value: str) -> None:
        self.engine.mic_volume = int(value) / 100

    def _set_gate(self, value: str) -> None:
        self.engine.gate = int(value) / 10000

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Elegir pista",
            filetypes=[
                ("Audio", "*.mp3 *.wav *.flac *.ogg *.m4a *.opus"),
                ("Todos", "*.*"),
            ],
        )
        if not path:
            return
        try:
            self.engine.load_track(path)
        except Exception as exc:
            messagebox.showerror("Error", f"No se pudo abrir el archivo:\n{exc}")
            return
        self.track_path = path
        self.track_var.set(os.path.basename(path))
        self.play_button.configure(text="Reproducir")
        self.lyrics = parse_lrc(os.path.splitext(path)[0] + ".lrc")
        self.lyric_index = -2
        self._render_lyrics(0.0)

    def _toggle(self) -> None:
        if self.source_var.get() != "live" and not self.track_path:
            messagebox.showinfo("Karaoke", "Primero elegí una pista.")
            return
        if self.engine.playing:
            self.engine.pause()
            self.play_button.configure(text="Reproducir")
        else:
            self.engine.play()
            self.play_button.configure(text="Pausa")

    def _stop(self) -> None:
        self.engine.stop()
        self.play_button.configure(text="Reproducir")
        self.lyric_index = -2
        self._render_lyrics(0.0)

    def _render_lyrics(self, position: float) -> None:
        if not self.lyrics:
            lines = ["(Sin archivo .lrc)" if not self.track_path else "(Esta pista no tiene letra)"]
        else:
            index = -1
            for i, (stamp, _) in enumerate(self.lyrics):
                if stamp <= position:
                    index = i
                else:
                    break
            if index != self.lyric_index:
                self.lyric_index = index
                start = max(0, index)
                lines = [text for _, text in self.lyrics[start:start + 4]]
            else:
                return
        self.lyric_text.configure(state=tk.NORMAL)
        self.lyric_text.delete("1.0", tk.END)
        self.lyric_text.insert("1.0", "\n".join(lines))
        self.lyric_text.configure(state=tk.DISABLED)

    def _tick(self) -> None:
        position = self.engine.position
        duration = self.engine.duration

        def fmt(seconds: float) -> str:
            seconds = int(seconds)
            return f"{seconds // 60:02d}:{seconds % 60:02d}"

        self.time_var.set(f"{fmt(position)} / {fmt(duration)}")
        self.progress["value"] = int(self.engine.progress * 1000)
        stats = self.engine.stats()
        connected = bool(stats["connected"])
        if connected:
            self.status_var.set(f"Micro: conectado · calidad {float(stats['quality']):.1f}%")
        else:
            self.status_var.set("Micro: esperando conexión")
        self.status_label.configure(foreground="#070" if connected else "#a00")
        source = "en vivo" if self.engine.live_active else "archivo"
        buffered = int(stats["buffered"])
        self.stats_var.set(
            f"Fuente: {source} | paquetes: {stats['received']} | perdidos: {stats['lost']} | "
            f"buffer: {buffered} ({buffered * 20} ms)"
        )
        self._ip_ticks += 1
        if self._ip_ticks >= 20:
            self._ip_ticks = 0
            ip = self.engine.local_ip()
            if ip != self.ip_var.get():
                self.ip_var.set(ip)
                if self.server is not None:
                    self.server.set_ip(ip)
        if self.engine.playing or self.lyric_index == -1:
            self._render_lyrics(position)
        self.root.after(150, self._tick)

    def _on_close(self) -> None:
        if self._routing:
            set_default_render(self._previous_default or SPEAKER_FALLBACK)
        if self.server is not None:
            self.server.stop()
        self.engine.close()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = App(root)
    if app.failed:
        return
    root.mainloop()


if __name__ == "__main__":
    main()
