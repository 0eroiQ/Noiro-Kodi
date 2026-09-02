import base64
import hashlib
import ipaddress
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .security import _atomic_json_write


class ProError(RuntimeError):
    pass


def canonical_json(payload):
    """Return the exact bytes covered by a Noiro entitlement signature."""
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def verify_rsa_sha256(message, signature, public_key):
    """Verify RSA PKCS#1 v1.5 SHA-256 using Python's standard library."""
    try:
        modulus = int(public_key["n"], 16)
        exponent = int(public_key.get("e", "10001"), 16)
    except (KeyError, TypeError, ValueError):
        return False
    length = (modulus.bit_length() + 7) // 8
    if len(signature) != length:
        return False
    encoded = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(length, "big")
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(message).digest()
    padding_length = length - len(digest_info) - 3
    if padding_length < 8:
        return False
    expected = b"\x00\x01" + (b"\xff" * padding_length) + b"\x00" + digest_info
    return encoded == expected


def validate_base_url(value):
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        raise ProError("The Noiro account service address is empty")
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ProError("Use a complete HTTPS address for the Noiro account service")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProError("The Noiro account service address contains unsupported fields")
    if parsed.scheme == "http":
        host = parsed.hostname.lower()
        local = host == "localhost" or host.endswith(".local")
        try:
            address = ipaddress.ip_address(host)
            local = local or address.is_private or address.is_loopback
        except ValueError:
            pass
        if not local:
            raise ProError("Plain HTTP is allowed only for a private-LAN development server")
    return raw


def public_entitlement(payload):
    features = sorted(set(item for item in (payload.get("features") or []) if isinstance(item, str)))
    plan = payload.get("plan") if payload.get("plan") in ("free", "pro") else "free"
    return {
        "account_id": payload.get("account_id"),
        "device_id": payload.get("device_id"),
        "plan": plan,
        "pro": plan == "pro",
        "features": features,
        "issued_at": int(payload.get("issued_at") or 0),
        "expires_at": int(payload.get("expires_at") or 0),
    }


def verify_entitlement(envelope, public_key, device_id, now=None, verifier=verify_rsa_sha256):
    if not isinstance(envelope, dict) or envelope.get("schema") != 1:
        raise ProError("The Noiro entitlement format is not supported")
    payload = envelope.get("payload")
    signature_text = envelope.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature_text, str):
        raise ProError("The Noiro entitlement is incomplete")
    try:
        signature = base64.b64decode(signature_text.encode("ascii"), validate=True)
    except (ValueError, UnicodeError):
        raise ProError("The Noiro entitlement signature is malformed")
    if not verifier(canonical_json(payload), signature, public_key):
        raise ProError("The Noiro entitlement signature is invalid")
    try:
        current = int(time.time() if now is None else now)
        issued_at = int(payload.get("issued_at") or 0)
        expires_at = int(payload.get("expires_at") or 0)
    except (TypeError, ValueError):
        raise ProError("The Noiro entitlement times are invalid")
    if not isinstance(payload.get("account_id"), str) or not payload.get("account_id"):
        raise ProError("The Noiro entitlement account is invalid")
    if payload.get("device_id") != device_id:
        raise ProError("This Noiro entitlement belongs to another device")
    if payload.get("plan") not in ("free", "pro"):
        raise ProError("The Noiro entitlement plan is invalid")
    if issued_at <= 0 or issued_at > current + 300:
        raise ProError("The Noiro entitlement issue time is invalid")
    if expires_at <= current:
        raise ProError("The Noiro entitlement has expired")
    if not isinstance(payload.get("features"), list) or any(
            not isinstance(item, str) or not item or len(item) > 64 for item in payload.get("features")):
        raise ProError("The Noiro entitlement feature list is invalid")
    return public_entitlement(payload)


class EntitlementStore(object):
    def __init__(self, path, public_key_path, device_id, verifier=verify_rsa_sha256):
        self.path = path
        self.public_key_path = public_key_path
        self.device_id = device_id
        self.verifier = verifier

    def _public_key(self):
        try:
            with open(self.public_key_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError) as error:
            raise ProError("The Noiro entitlement public key is unavailable: %s" % error)

    def save(self, envelope):
        verified = verify_entitlement(envelope, self._public_key(), self.device_id, verifier=self.verifier)
        _atomic_json_write(self.path, envelope)
        return verified

    def read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                envelope = json.load(handle)
        except (OSError, ValueError):
            return None
        return verify_entitlement(envelope, self._public_key(), self.device_id, verifier=self.verifier)

    def clear(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass


class ProClient(object):
    def __init__(self, base_url, access_token=None, timeout=20, opener=None):
        self.base_url = validate_base_url(base_url)
        self.access_token = access_token
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    def _request(self, method, path, payload=None, authorized=False):
        body = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "Noiro-Kodi/0.2.1",
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authorized:
            if not self.access_token:
                raise ProError("This Vero is not linked to a Noiro account")
            headers["Authorization"] = "Bearer %s" % self.access_token
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read()
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("response is not an object")
            return value
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise ProError("The Noiro account link is no longer valid")
            raise ProError("Noiro account service returned HTTP %d" % error.code)
        except (urllib.error.URLError, OSError, ValueError, UnicodeError) as error:
            raise ProError("Noiro account service is unavailable: %s" % error)

    def create_link(self, device_id, device_name="Vero 4K+"):
        value = self._request("POST", "/v1/device-links", {
            "device_id": device_id,
            "device_name": device_name,
            "platform": "osmc-kodi",
        })
        required = ("device_code", "user_code", "verification_uri", "expires_in")
        if any(not value.get(key) for key in required):
            raise ProError("Noiro account service returned an incomplete link code")
        now = int(time.time())
        return {
            "device_code": value["device_code"],
            "user_code": value["user_code"],
            "verification_uri": value["verification_uri"],
            "verification_uri_complete": value.get("verification_uri_complete") or value["verification_uri"],
            "qrcode": value.get("qrcode"),
            "expires_at": now + min(int(value.get("expires_in") or 300), 600),
            "poll_interval": max(2, min(int(value.get("interval") or 2), 10)),
        }

    def poll_link(self, device_code):
        return self._request("GET", "/v1/device-links/%s" % urllib.parse.quote(str(device_code), safe=""))

    def entitlement(self):
        return self._request("GET", "/v1/entitlements/current", authorized=True)
