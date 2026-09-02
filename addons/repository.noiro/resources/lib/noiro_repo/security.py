import hashlib
import json
import os
import tempfile


def atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".noiro-repo-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def hash_pin(pin, salt=None):
    import base64
    import secrets
    value = str(pin)
    if not value.isdigit() or len(value) != 4:
        raise ValueError("Maintenance PIN must contain exactly four digits")
    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(value.encode("utf-8"), salt=salt_bytes, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$%s$%s" % (
        base64.urlsafe_b64encode(salt_bytes).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def verify_rsa_sha256(message, signature, public_key):
    """Verify an RSA PKCS#1 v1.5 SHA-256 signature using only stdlib."""
    modulus = int(public_key["n"], 16)
    exponent = int(public_key.get("e", "10001"), 16)
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
