#!/usr/bin/env python3
import argparse
import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons/repository.noiro/resources/lib"))

from noiro_repo.security import verify_rsa_sha256  # noqa: E402


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Verify a complete Noiro release directory")
    parser.add_argument("--artifacts", default=str(ROOT / "artifacts"))
    parser.add_argument("--require-signature", action="store_true")
    args = parser.parse_args()
    directory = Path(args.artifacts)
    manifest_path = directory / "release-manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if manifest.get("schema") != 1 or manifest.get("kodi_major") != 21:
        raise SystemExit("invalid release schema or Kodi major")
    for item in manifest.get("artifacts") or []:
        path = directory / item["name"]
        if not path.is_file() or path.stat().st_size != int(item["size"]) or sha256(path) != item["sha256"]:
            raise SystemExit("artifact verification failed: %s" % item["name"])
        if path.suffix == ".zip" and item.get("kind") == "kodi-addon":
            with zipfile.ZipFile(path) as archive:
                addon_id = item["addon_id"]
                for entry in archive.infolist():
                    parts = [part for part in entry.filename.replace("\\", "/").split("/") if part]
                    if not parts or parts[0] != addon_id or ".." in parts:
                        raise SystemExit("unsafe ZIP entry in %s" % item["name"])
    expected_index = hashlib.sha256((directory / "addons.xml").read_bytes()).hexdigest()
    actual_index = (directory / "addons.xml.sha256").read_text(encoding="ascii").strip()
    if expected_index != actual_index:
        raise SystemExit("addons.xml checksum mismatch")
    signature_path = directory / "release-manifest.sig"
    if args.require_signature and not signature_path.is_file():
        raise SystemExit("release signature is missing")
    if signature_path.is_file():
        public_key = json.loads((ROOT / "addons/repository.noiro/resources/public_key.json").read_text(encoding="utf-8"))
        signature = base64.b64decode(signature_path.read_bytes().strip(), validate=True)
        if not verify_rsa_sha256(manifest_bytes, signature, public_key):
            raise SystemExit("release signature verification failed")
    print("Verified %d release artifacts%s" % (
        len(manifest.get("artifacts") or []),
        " and RSA signature" if signature_path.is_file() else "",
    ))


if __name__ == "__main__":
    main()
