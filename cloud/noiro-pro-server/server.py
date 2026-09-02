#!/usr/bin/env python3
"""Local reference implementation of the Noiro device-link contract.

This server activates test Pro entitlements only. It does not take payments and
must not be exposed to the public internet. A production service should keep
the same device API while replacing the activation page with authenticated
account and payment-provider flows.
"""

import argparse
import base64
import html
import json
import os
import secrets
import shutil
import string
import subprocess
import tempfile
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def canonical_json(payload):
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sign_payload(payload, private_key):
    message = canonical_json(payload)
    with tempfile.NamedTemporaryFile() as source, tempfile.NamedTemporaryFile() as signature:
        source.write(message)
        source.flush()
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", private_key, "-out", signature.name, source.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("OpenSSL could not sign the entitlement")
        return base64.b64encode(Path(signature.name).read_bytes()).decode("ascii")


class LinkState(object):
    ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    def __init__(self, private_key):
        self.private_key = private_key
        self.lock = threading.RLock()
        self.links = {}
        self.codes = {}
        self.tokens = {}

    def _user_code(self):
        while True:
            value = "".join(secrets.choice(self.ALPHABET) for _ in range(8))
            value = value[:4] + "-" + value[4:]
            if value not in self.codes:
                return value

    def create(self, device_id, device_name):
        now = int(time.time())
        with self.lock:
            device_code = secrets.token_urlsafe(32)
            user_code = self._user_code()
            link = {
                "device_code": device_code,
                "user_code": user_code,
                "device_id": device_id,
                "device_name": device_name,
                "expires_at": now + 300,
                "status": "pending",
            }
            self.links[device_code] = link
            self.codes[user_code] = device_code
            return dict(link)

    def poll(self, device_code):
        with self.lock:
            link = self.links.get(device_code)
            if not link or int(link["expires_at"]) <= int(time.time()):
                return {"status": "expired"}
            if link["status"] != "linked":
                return {"status": "pending", "code": 101}
            return {
                "status": "linked",
                "access_token": link["access_token"],
                "account": {"id": link["account_id"]},
            }

    def link(self, device_code):
        with self.lock:
            link = self.links.get(device_code)
            if not link or int(link["expires_at"]) <= int(time.time()):
                return None
            return dict(link)

    def activate(self, user_code, email):
        normalized = str(user_code or "").strip().upper()
        with self.lock:
            device_code = self.codes.get(normalized)
            link = self.links.get(device_code)
            if not link or int(link["expires_at"]) <= int(time.time()):
                return None
            if link.get("status") == "linked":
                return dict(link)
            access_token = secrets.token_urlsafe(40)
            account_id = "acct_" + uuid.uuid4().hex
            link.update({
                "status": "linked",
                "access_token": access_token,
                "account_id": account_id,
                "email": str(email or "local-test@noiro.invalid")[:254],
            })
            self.tokens[access_token] = link
            return dict(link)

    def entitlement(self, access_token):
        with self.lock:
            link = self.tokens.get(access_token)
            if not link:
                return None
            now = int(time.time())
            payload = {
                "account_id": link["account_id"],
                "device_id": link["device_id"],
                "expires_at": now + (30 * 24 * 60 * 60),
                "features": ["pro_badge", "pro_preview"],
                "issued_at": now,
                "plan": "pro",
            }
        return {
            "schema": 1,
            "payload": payload,
            "signature": sign_payload(payload, self.private_key),
        }


class ProServer(ThreadingHTTPServer):
    def __init__(self, address, handler, state, base_url=None):
        super().__init__(address, handler)
        self.state = state
        self.base_url = str(base_url or "").rstrip("/")


class Handler(BaseHTTPRequestHandler):
    server_version = "NoiroProDev/0.2"

    def log_message(self, message, *args):
        # Never include request headers or bearer tokens in logs.
        print("%s - %s" % (self.address_string(), message % args))

    def _json(self, status, payload):
        value = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(value)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(value)

    def _html(self, status, body):
        value = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(value)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        self.end_headers()
        self.wfile.write(value)

    def _bytes(self, status, content_type, value):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(value)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(value)

    def _body(self):
        length = min(int(self.headers.get("Content-Length", "0")), 32768)
        return self.rfile.read(length)

    def _base_url(self):
        return self.server.base_url or ("http://" + self.headers.get("Host", "127.0.0.1:8098"))

    def _activation_form(self, code="", error=""):
        message = "<p class='error'>%s</p>" % html.escape(error) if error else ""
        return """<!doctype html><html><head><meta name='viewport' content='width=device-width'>
<title>Noiro Pro test</title><style>body{background:#080b12;color:#fff;font-family:-apple-system,sans-serif;max-width:620px;margin:40px auto;padding:22px}input{box-sizing:border-box;width:100%%;padding:14px;margin:8px 0 18px;background:#151a25;color:#fff;border:1px solid #394157;border-radius:9px}button{padding:14px 22px;background:#8f7cff;color:#fff;border:0;border-radius:9px;font-weight:700}.warning{color:#ffcf70}.error{color:#ff8c8c}</style></head><body>
<h1>Noiro Pro local test</h1><p class='warning'>Test entitlement only. No payment is taken.</p>%s
<form method='post' action='/activate'><label>Code shown on Vero</label><input name='code' value='%s' required autocomplete='one-time-code'>
<label>Email (optional test label)</label><input name='email' type='email' autocomplete='email'>
<button type='submit'>Activate test Pro</button></form></body></html>""" % (message, html.escape(code))

    def do_POST(self):
        if self.path == "/v1/device-links":
            try:
                payload = json.loads(self._body().decode("utf-8"))
                device_id = str(payload.get("device_id") or "")
                if not device_id or len(device_id) > 128:
                    raise ValueError("invalid device")
            except (ValueError, UnicodeError):
                self._json(400, {"error": "invalid_request"})
                return
            link = self.server.state.create(device_id, str(payload.get("device_name") or "Vero 4K+"))
            base = self._base_url()
            self._json(201, {
                "device_code": link["device_code"],
                "user_code": link["user_code"],
                "verification_uri": base + "/activate",
                "verification_uri_complete": base + "/activate/" + urllib.parse.quote(link["user_code"]),
                "qrcode": (
                    base + "/v1/device-links/" + urllib.parse.quote(link["device_code"], safe="") + "/qr.png"
                    if shutil.which("qrencode") else None
                ),
                "expires_in": 300,
                "interval": 2,
            })
            return
        if self.path == "/activate":
            form = urllib.parse.parse_qs(self._body().decode("utf-8"), keep_blank_values=True)
            code = (form.get("code") or [""])[0]
            email = (form.get("email") or [""])[0]
            link = self.server.state.activate(code, email)
            if not link:
                self._html(400, self._activation_form(code, "That code is invalid or expired."))
                return
            self._html(200, """<!doctype html><html><body style='background:#080b12;color:#fff;font-family:sans-serif;text-align:center;padding:60px'><h1>Test Pro activated</h1><p>Return to your Vero. No payment was taken.</p></body></html>""")
            return
        self._json(404, {"error": "not_found"})

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ready": True, "mode": "local-test", "payments": False})
            return
        if self.path == "/activate":
            self._html(200, self._activation_form())
            return
        if self.path.startswith("/activate/"):
            code = urllib.parse.unquote(self.path.split("/activate/", 1)[1]).upper()
            self._html(200, self._activation_form(code))
            return
        if self.path.startswith("/v1/device-links/"):
            code = urllib.parse.unquote(self.path.split("/v1/device-links/", 1)[1])
            if code.endswith("/qr.png"):
                code = code[:-7]
                link = self.server.state.link(code)
                executable = shutil.which("qrencode")
                if not link or not executable:
                    self._json(404, {"error": "not_found"})
                    return
                target = self._base_url() + "/activate/" + urllib.parse.quote(link["user_code"])
                result = subprocess.run(
                    [executable, "-o", "-", "-t", "PNG", "-s", "9", "-m", "2", target],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                if result.returncode or not result.stdout:
                    self._json(500, {"error": "qr_failed"})
                    return
                self._bytes(200, "image/png", result.stdout)
                return
            self._json(200, self.server.state.poll(code))
            return
        if self.path == "/v1/entitlements/current":
            authorization = self.headers.get("Authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else ""
            envelope = self.server.state.entitlement(token)
            if not envelope:
                self._json(401, {"error": "invalid_token"})
                return
            self._json(200, envelope)
            return
        self._json(404, {"error": "not_found"})


def main():
    parser = argparse.ArgumentParser(description="Run the local Noiro Pro contract test server")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--base-url", default=None)
    parser.add_argument(
        "--private-key",
        default=str(Path.home() / ".config/noiro-kodi/pro-entitlement-private.pem"),
    )
    args = parser.parse_args()
    private_key = str(Path(args.private_key).expanduser().resolve())
    if not os.path.isfile(private_key):
        raise SystemExit("Entitlement key is missing. Run scripts/generate_pro_key.py first.")
    if args.bind not in ("127.0.0.1", "::1", "localhost"):
        print("WARNING: local test mode is visible on the LAN; do not expose this port to the internet.")
    server = ProServer((args.bind, args.port), Handler, LinkState(private_key), args.base_url)
    print("Noiro Pro local test server: http://%s:%d" % (args.bind, args.port))
    print("Payments: disabled (test entitlements only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
