#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="Create a local Noiro release-signing key")
    parser.add_argument("--private-key", default=str(Path.home() / ".config/noiro-kodi/release-private.pem"))
    args = parser.parse_args()
    private = Path(args.private_key).expanduser().resolve()
    private.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not private.exists():
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(private)], check=True)
        os.chmod(private, 0o600)
    output = subprocess.check_output(["openssl", "rsa", "-in", str(private), "-noout", "-modulus"], text=True)
    match = re.search(r"Modulus=([0-9A-Fa-f]+)", output)
    if not match:
        raise SystemExit("Could not read RSA modulus")
    public = {"algorithm": "RSA-3072-PKCS1-v1_5-SHA256", "e": "10001", "n": match.group(1).lower()}
    path = ROOT / "addons/repository.noiro/resources/public_key.json"
    path.write_text(json.dumps(public, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(private)


if __name__ == "__main__":
    main()
