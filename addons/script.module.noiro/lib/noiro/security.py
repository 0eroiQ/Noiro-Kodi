import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading


def _atomic_json_write(path, payload, mode=0o600):
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".noiro-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SecretStore(object):
    """Permission-protected local secret store.

    OSMC has no platform keychain. This store deliberately makes no claim of
    hardware-backed encryption; it restricts the file to the osmc user and
    keeps secrets out of Kodi settings and logs.
    """

    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()

    def _read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, key, default=None):
        with self._lock:
            return self._read().get(key, default)

    def set(self, key, value):
        with self._lock:
            payload = self._read()
            if value is None:
                payload.pop(key, None)
            else:
                payload[key] = value
            _atomic_json_write(self.path, payload)

    def delete_prefix(self, prefix):
        with self._lock:
            payload = self._read()
            for key in list(payload):
                if key.startswith(prefix):
                    del payload[key]
            _atomic_json_write(self.path, payload)


def hash_pin(pin, salt=None):
    value = str(pin)
    if not value.isdigit() or len(value) != 4:
        raise ValueError("PIN must contain exactly four digits")
    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(value.encode("utf-8"), salt=salt_bytes, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$%s$%s" % (
        base64.urlsafe_b64encode(salt_bytes).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_pin(pin, encoded):
    try:
        algorithm, salt_text, digest_text = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(str(pin).encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(expected, actual)
    except (TypeError, ValueError):
        return False


def new_nonce(bytes_count=24):
    return secrets.token_urlsafe(bytes_count)
