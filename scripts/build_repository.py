#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import stat
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EPOCH = (2020, 1, 1, 0, 0, 0)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def zip_tree(source, destination, prefix):
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative = Path(prefix) / path.relative_to(source)
            info = zipfile.ZipInfo(str(relative).replace(os.sep, "/"), EPOCH)
            mode = path.stat().st_mode
            permissions = 0o755 if mode & stat.S_IXUSR else 0o644
            info.external_attr = (stat.S_IFREG | permissions) << 16
            archive.writestr(info, path.read_bytes())


def source_archive(destination):
    excluded = {".git", "artifacts", "target", "__pycache__"}
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or any(part in excluded for part in path.parts) or path.suffix == ".pyc":
                continue
            relative = Path("Noiro-Kodi-source") / path.relative_to(ROOT)
            info = zipfile.ZipInfo(str(relative).replace(os.sep, "/"), EPOCH)
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, path.read_bytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=(ROOT / "VERSION").read_text(encoding="utf-8").strip())
    parser.add_argument("--output", default=str(ROOT / "artifacts"))
    parser.add_argument("--require-native", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    pin = json.loads((ROOT / "native/upstream-core.json").read_text(encoding="utf-8"))
    vendor_metadata = ROOT / "native/vendor/stremio-core/NOIRO_UPSTREAM.json"
    vendor_license = ROOT / "native/vendor/stremio-core/LICENSE.md"
    if not vendor_metadata.is_file() or not vendor_license.is_file():
        raise SystemExit("the exact Stremio core corresponding-source snapshot is missing")
    vendor = json.loads(vendor_metadata.read_text(encoding="utf-8"))
    if vendor.get("commit") != pin.get("stremio_core_commit"):
        raise SystemExit("the vendored Stremio core does not match the pinned commit")
    if args.require_native and not (ROOT / "addons/script.service.noiro/resources/bin/linux-armhf/noiro-engine").is_file():
        raise SystemExit("armhf native engine is required but missing")
    if (ROOT / "addons/script.service.noiro/resources/bin/linux-armhf/noiro-engine").is_file() and not (ROOT / "native/noiro-engine/Cargo.lock").is_file():
        raise SystemExit("native binary exists without its complete pinned source snapshot")
    roots = []
    artifacts = []
    for addon_dir in sorted((ROOT / "addons").iterdir()):
        if not addon_dir.is_dir() or not (addon_dir / "addon.xml").is_file():
            continue
        element = ET.parse(addon_dir / "addon.xml").getroot()
        if element.get("version") != args.version:
            raise SystemExit("%s version does not match %s" % (addon_dir.name, args.version))
        roots.append(element)
        name = "%s-%s.zip" % (addon_dir.name, args.version)
        destination = output / name
        zip_tree(addon_dir, destination, addon_dir.name)
        artifacts.append({
            "name": name,
            "addon_id": addon_dir.name,
            "kind": "kodi-addon",
            "sha256": digest(destination),
            "size": destination.stat().st_size,
        })
        if addon_dir.name == "repository.noiro":
            bootstrap = output / "repository.noiro.zip"
            bootstrap.write_bytes(destination.read_bytes())
    addons_root = ET.Element("addons")
    for element in roots:
        addons_root.append(element)
    xml_body = ET.tostring(addons_root, encoding="utf-8", xml_declaration=True)
    addons_xml = output / "addons.xml"
    addons_xml.write_bytes(xml_body)
    checksum = output / "addons.xml.sha256"
    checksum.write_text(hashlib.sha256(xml_body).hexdigest() + "\n", encoding="ascii")
    for path, kind in ((addons_xml, "repository-index"), (checksum, "repository-checksum")):
        artifacts.append({"name": path.name, "kind": kind, "sha256": digest(path), "size": path.stat().st_size})
    source = output / ("Noiro-Kodi-%s-source.zip" % args.version)
    source_archive(source)
    artifacts.append({"name": source.name, "kind": "corresponding-source", "sha256": digest(source), "size": source.stat().st_size})
    manifest = {
        "schema": 1,
        "product": "Noiro-Kodi",
        "version": args.version,
        "kodi_major": 21,
        "generated_at": int(os.environ.get("SOURCE_DATE_EPOCH", str(int(time.time())))),
        "stremio_core_commit": pin["stremio_core_commit"],
        "noiro_core_commit": pin["commit"],
        "artifacts": sorted(artifacts, key=lambda item: item["name"]),
    }
    manifest_path = output / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (output / "SHA256SUMS").write_text(
        "".join("%s  %s\n" % (item["sha256"], item["name"]) for item in manifest["artifacts"]),
        encoding="ascii",
    )
    print("Built %d release artifacts in %s" % (len(artifacts), output))


if __name__ == "__main__":
    main()
