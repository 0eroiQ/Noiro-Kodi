#!/usr/bin/env python3
import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def build_tree(artifacts, output):
    artifacts = Path(artifacts).resolve()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    manifest = json.loads((artifacts / "release-manifest.json").read_text(encoding="utf-8"))
    for name in ("addons.xml", "addons.xml.sha256"):
        shutil.copy2(artifacts / name, output / name)

    copied = 0
    for item in manifest.get("artifacts") or []:
        if item.get("kind") != "kodi-addon" or item.get("addon_id") != "repository.noiro":
            continue
        source = artifacts / item["name"]
        destination_dir = output / item["addon_id"]
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / item["name"]
        shutil.copy2(source, destination)
        destination.with_name(destination.name + ".sha256").write_text(
            item["sha256"] + "\n",
            encoding="ascii",
        )
        copied += 1
    if copied != 1:
        raise RuntimeError("The Kodi repository tree must contain exactly one bootstrap package")
    return copied


def main():
    parser = argparse.ArgumentParser(description="Build Kodi's addon-id directory layout")
    parser.add_argument("--artifacts", default=str(ROOT / "artifacts"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    copied = build_tree(args.artifacts, args.output)
    print("Published %d Kodi bootstrap package" % copied)


if __name__ == "__main__":
    main()
