#!/usr/bin/env python3
import argparse
import base64
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="Sign the canonical Noiro release manifest")
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--manifest", default=str(ROOT / "artifacts/release-manifest.json"))
    parser.add_argument("--output", default=str(ROOT / "artifacts/release-manifest.sig"))
    args = parser.parse_args()
    with tempfile.NamedTemporaryFile() as signature:
        subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", args.private_key, "-out", signature.name, args.manifest],
            check=True,
        )
        raw = Path(signature.name).read_bytes()
    Path(args.output).write_text(base64.b64encode(raw).decode("ascii") + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
