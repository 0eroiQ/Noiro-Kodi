#!/usr/bin/env python3
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/Stremio/stremio-core.git"


def run(command, cwd=None, capture=False):
    result = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def main():
    pin = json.loads((ROOT / "native" / "upstream-core.json").read_text(encoding="utf-8"))
    commit = pin["stremio_core_commit"]
    destination = ROOT / "native" / "vendor" / "stremio-core"
    with tempfile.TemporaryDirectory(prefix="noiro-stremio-core-") as temporary:
        checkout = Path(temporary) / "stremio-core"
        run(["git", "clone", "--filter=blob:none", "--no-checkout", REPOSITORY, str(checkout)])
        run(["git", "checkout", "--detach", commit], cwd=checkout)
        actual = run(["git", "rev-parse", "HEAD"], cwd=checkout, capture=True)
        if actual != commit:
            raise SystemExit("Stremio core checkout did not match the pinned commit")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(
            checkout,
            destination,
            ignore=shutil.ignore_patterns(".git", "target", "node_modules", ".DS_Store"),
        )
    (destination / "NOIRO_UPSTREAM.json").write_text(
        json.dumps({"repository": REPOSITORY, "commit": commit}, indent=2) + "\n",
        encoding="utf-8",
    )
    print("Vendored Stremio core %s" % commit)


if __name__ == "__main__":
    main()
