#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command, cwd=None):
    subprocess.run(command, cwd=cwd, check=True)


def main():
    parser = argparse.ArgumentParser(description="Fetch the exactly pinned Noiro Stremio core snapshot")
    parser.add_argument("--output", default=str(ROOT / "native" / "stremiox-core"))
    parser.add_argument("--local-repo", default=os.environ.get("NOIRO_APPLE_REPO"))
    args = parser.parse_args()
    pin = json.loads((ROOT / "native" / "upstream-core.json").read_text(encoding="utf-8"))
    output = Path(args.output).resolve()
    with tempfile.TemporaryDirectory(prefix="noiro-core-") as temporary:
        checkout = Path(temporary) / "repo"
        if args.local_repo:
            run(["git", "clone", "--no-checkout", args.local_repo, str(checkout)])
        else:
            run(["git", "clone", "--filter=blob:none", "--no-checkout", pin["repository"], str(checkout)])
        run(["git", "checkout", "--detach", pin["commit"]], cwd=checkout)
        source = checkout / pin["path"]
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(source, output, ignore=shutil.ignore_patterns("target", ".DS_Store"))
    cargo = output / "Cargo.toml"
    value = cargo.read_text(encoding="utf-8")
    value = value.replace('crate-type = ["staticlib", "cdylib"]', 'crate-type = ["rlib", "staticlib", "cdylib"]')
    value = value.replace(
        'git = "https://github.com/Stremio/stremio-core", branch = "development"',
        'git = "https://github.com/Stremio/stremio-core", rev = "31393600895d0a1cee94966bd806aab41cd90e4e"',
    )
    cargo.write_text(value, encoding="utf-8")
    (output / "NOIRO_SNAPSHOT.json").write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
