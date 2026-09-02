import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons/script.module.noiro/lib"))

from noiro.pro import ProClient, verify_entitlement  # noqa: E402


SPEC = importlib.util.spec_from_file_location(
    "noiro_pro_server",
    ROOT / "cloud/noiro-pro-server/server.py",
)
SERVER_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER_MODULE)


class QuietHandler(SERVER_MODULE.Handler):
    def log_message(self, _message, *_args):
        return


class ProServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.private_key = Path(self.temp.name, "entitlement-private.pem")
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(self.private_key)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        modulus_output = subprocess.check_output(
            ["openssl", "rsa", "-in", str(self.private_key), "-noout", "-modulus"],
            text=True,
        )
        match = re.search(r"Modulus=([0-9A-Fa-f]+)", modulus_output)
        self.public_key = {"e": "10001", "n": match.group(1).lower()}
        state = SERVER_MODULE.LinkState(str(self.private_key))
        self.server = SERVER_MODULE.ProServer(("127.0.0.1", 0), QuietHandler, state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def test_device_link_and_real_signed_entitlement(self):
        client = ProClient(self.base_url)
        session = client.create_link("vero-test-device")
        if session.get("qrcode"):
            with urllib.request.urlopen(session["qrcode"], timeout=5) as response:
                self.assertTrue(response.read(8).startswith(b"\x89PNG"))
        self.assertEqual(client.poll_link(session["device_code"])["code"], 101)
        body = urllib.parse.urlencode({"code": session["user_code"], "email": "test@example.test"}).encode("utf-8")
        with urllib.request.urlopen(self.base_url + "/activate", data=body, timeout=5) as response:
            self.assertEqual(response.status, 200)
        linked = client.poll_link(session["device_code"])
        self.assertEqual(linked["status"], "linked")
        client.access_token = linked["access_token"]
        envelope = client.entitlement()
        verified = verify_entitlement(envelope, self.public_key, "vero-test-device")
        self.assertTrue(verified["pro"])
        self.assertIn("pro_badge", verified["features"])


if __name__ == "__main__":
    unittest.main()
