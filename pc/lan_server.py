from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace


def _page(cfg: SimpleNamespace) -> str:
    ip = cfg.ip
    udp = cfg.udp_port
    intent = (
        f"intent://connect?ip={ip}&port={udp}"
        "#Intent;scheme=karaoke;package=com.karaoke.mic;end"
    )
    direct = f"karaoke://connect?ip={ip}&port={udp}"
    if cfg.apk_bytes:
        apk_button = '<a class="btn secondary" href="/app.apk">1. Descargar app (APK)</a>'
    else:
        apk_button = '<p class="muted">La app no está disponible para descargar desde esta PC.</p>'
    return f"""<!doctype html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Karaoke Móvil</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin:0; font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  background: radial-gradient(circle at 20% 0%, #3b1d7a 0%, #0b0716 55%, #05030c 100%);
  color:#f5f3ff; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px; }}
.card {{ width:100%; max-width:460px; background:rgba(23,17,38,.92); border:1px solid #2c2347;
  border-radius:24px; padding:28px 24px; box-shadow:0 20px 60px rgba(0,0,0,.45); }}
h1 {{ margin:0 0 6px; font-size:26px; letter-spacing:.3px; }}
.sub {{ margin:0 0 18px; color:#b9afd6; font-size:14px; }}
.info {{ display:flex; gap:10px; margin:0 0 20px; }}
.chip {{ flex:1; background:#0f0b1e; border:1px solid #2c2347; border-radius:14px; padding:10px 12px; }}
.chip small {{ display:block; color:#9a8fc0; font-size:11px; text-transform:uppercase; letter-spacing:.08em; }}
.chip b {{ font-size:16px; }}
.btn {{ display:block; text-align:center; text-decoration:none; font-weight:700; font-size:16px;
  padding:15px 16px; border-radius:16px; margin:10px 0; color:#fff;
  background:linear-gradient(135deg,#8b5cf6,#ec4899); box-shadow:0 10px 24px rgba(139,92,246,.35); }}
.btn.secondary {{ background:#1b1430; border:1px solid #3b2f63; box-shadow:none; }}
.steps {{ margin:18px 0 0; padding-left:20px; color:#cfc6e8; font-size:14px; line-height:1.55; }}
.steps b {{ color:#fff; }}
.muted {{ color:#9a8fc0; font-size:12px; text-align:center; margin-top:16px; }}
</style></head><body>
<main class="card">
  <h1>Karaoke Móvil</h1>
  <p class="sub">Usá tu celular como micrófono por Wi‑Fi.</p>
  <div class="info">
    <div class="chip"><small>PC</small><b>{ip}</b></div>
    <div class="chip"><small>Puerto</small><b>{udp}</b></div>
  </div>
  {apk_button}
  <a class="btn" href="{intent}">2. Abrir Karaoke Móvil</a>
  <ol class="steps">
    <li>Instalá la app (si ya la tenés, saltá este paso).</li>
    <li>Tocá <b>Abrir Karaoke Móvil</b>: se conecta sola con estos datos.</li>
    <li>Dejá la app abierta o apagá la pantalla; el micrófono sigue activo.</li>
  </ol>
  <a class="btn secondary" href="{direct}">Abrir con enlace directo</a>
  <p class="muted">PC y celular tienen que estar en la misma Wi‑Fi.</p>
</main>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "KaraokePC/1.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        cfg = self.server.cfg  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        if path in ("/app.apk", "/karaoke-mic.apk"):
            data = cfg.apk_bytes
            if not data:
                self.send_error(404, "APK no disponible")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.android.package-archive")
            self.send_header("Content-Disposition", 'attachment; filename="karaoke-mic.apk"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
            return
        if path in ("/", "/index.html"):
            body = _page(cfg).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, cfg):
        super().__init__(address, handler)
        self.cfg = cfg


class LanPageServer:
    def __init__(self, udp_port: int, apk_path: str, ip: str = ""):
        cfg = SimpleNamespace(
            ip=ip or "127.0.0.1",
            udp_port=udp_port,
            http_port=udp_port,
            apk_bytes=None,
        )
        if os.path.isfile(apk_path):
            try:
                with open(apk_path, "rb") as handle:
                    cfg.apk_bytes = handle.read()
            except OSError:
                cfg.apk_bytes = None
        self.cfg = cfg
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def started(self) -> bool:
        return self._httpd is not None

    @property
    def page_url(self) -> str:
        return f"http://{self.cfg.ip}:{self.cfg.http_port}/"

    def set_ip(self, ip: str) -> None:
        if ip:
            self.cfg.ip = ip

    def start(self) -> bool:
        if self._httpd is not None:
            return True
        for candidate in range(self.cfg.udp_port, self.cfg.udp_port + 10):
            try:
                httpd = _Server(("0.0.0.0", candidate), _Handler, self.cfg)
            except OSError:
                continue
            self.cfg.http_port = candidate
            self._httpd = httpd
            self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            self._thread.start()
            return True
        return False

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
