import html
import json
import os
import secrets
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .github import GitHubReleaseClient, ReleaseError
from .security import atomic_json, hash_pin


class BootstrapServer(object):
    def __init__(self, data_dir, public_key_path, on_configured=None, port=64892):
        self.data_dir = data_dir
        self.public_key_path = public_key_path
        self.on_configured = on_configured
        self.port = port
        self.nonce = secrets.token_urlsafe(24)
        self.server = None
        self.thread = None

    @staticmethod
    def lan_ip():
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("1.1.1.1", 53))
            return probe.getsockname()[0]
        except OSError:
            try:
                return socket.gethostbyname(socket.gethostname())
            except OSError:
                return "127.0.0.1"
        finally:
            probe.close()

    @property
    def url(self):
        return "http://%s:%d/setup/%s" % (self.lan_ip(), self.port, self.nonce)

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _respond(self, status, body):
                value = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(value)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Options", "DENY")
                self.end_headers()
                self.wfile.write(value)

            def do_GET(self):
                if self.path != "/setup/" + owner.nonce:
                    self._respond(404, "Not found")
                    return
                self._respond(200, owner.form())

            def do_POST(self):
                if self.path != "/setup/" + owner.nonce:
                    self._respond(404, "Not found")
                    return
                try:
                    length = min(int(self.headers.get("Content-Length", "0")), 32768)
                    form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                    gemini = (form.get("gemini_api_key") or [""])[0].strip()
                    pin = (form.get("maintenance_pin") or [""])[0].strip()
                    client = GitHubReleaseClient(None, os.path.join(owner.data_dir, "cache"), owner.public_key_path)
                    if not client.validate_repository():
                        raise ValueError("The public 0eroiQ/Noiro-Kodi repository is unavailable")
                    pin_hash = hash_pin(pin)
                    atomic_json(os.path.join(owner.data_dir, "provisioning.json"), {
                        "gemini_api_key": gemini or None,
                        "maintenance_pin_hash": pin_hash,
                    })
                    atomic_json(os.path.join(owner.data_dir, "configured.json"), {
                        "configured": True,
                        "schema": 1,
                    })
                    owner.nonce = secrets.token_urlsafe(24)
                    self._respond(200, owner.success())
                    if owner.on_configured:
                        threading.Thread(target=owner.on_configured, daemon=True).start()
                except (ValueError, ReleaseError) as error:
                    self._respond(400, owner.form(str(error)))

        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="noiro-setup", daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=3)

    @staticmethod
    def form(error=None):
        message = "<p class='error'>%s</p>" % html.escape(error) if error else ""
        return """<!doctype html><html><head><meta name='viewport' content='width=device-width'>
<title>Noiro setup</title><style>body{background:#080b12;color:#fff;font-family:-apple-system,sans-serif;max-width:620px;margin:40px auto;padding:20px}input{box-sizing:border-box;width:100%%;padding:14px;margin:8px 0 18px;background:#151a25;color:white;border:1px solid #394157;border-radius:9px}button{padding:14px 22px;background:#8f7cff;color:#fff;border:0;border-radius:9px;font-weight:700}.error{color:#ff8c8c}</style></head><body>
<h1>NoiroTV setup</h1><p>These values are sent only across your local network to this Vero.</p>%s
<form method='post'><label>Gemini API key (optional)</label><input type='password' name='gemini_api_key' autocomplete='off'>
<label>Four-digit Maintenance PIN</label><input type='password' name='maintenance_pin' pattern='[0-9]{4}' inputmode='numeric' required autocomplete='off'>
<button type='submit'>Set up NoiroTV</button></form></body></html>""" % message

    @staticmethod
    def success():
        return """<!doctype html><html><head><meta name='viewport' content='width=device-width'><title>Noiro ready</title></head><body style='background:#080b12;color:white;font-family:sans-serif;text-align:center;padding:60px'><h1>NoiroTV is connected</h1><p>You can return to the television. Kodi is verifying the signed Noiro release.</p></body></html>"""
