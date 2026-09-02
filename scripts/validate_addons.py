#!/usr/bin/env python3
import argparse
import ast
import py_compile
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SKIN_WINDOWS = (
    "Startup.xml",
    "Home.xml",
    "MyVideoNav.xml",
    "VideoFullScreen.xml",
    "VideoOSD.xml",
    "DialogSeekBar.xml",
    "DialogSelect.xml",
    "DialogContextMenu.xml",
    "DialogConfirm.xml",
    "DialogKeyboard.xml",
    "DialogNumeric.xml",
    "DialogNotification.xml",
    "DialogOK.xml",
    "DialogTextViewer.xml",
    "DialogVolumeBar.xml",
    "DialogBusy.xml",
    "DialogExtendedProgressBar.xml",
    "DialogMuteBug.xml",
    "DialogButtonMenu.xml",
    "Pointer.xml",
    "Font.xml",
    "Includes.xml",
)


def fail(message):
    print("ERROR:", message, file=sys.stderr)
    return 1


def main():
    expected = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    errors = 0
    manifests = {}
    for path in sorted((ROOT / "addons").glob("*/addon.xml")):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as error:
            errors += fail("%s: %s" % (path, error))
            continue
        addon_id = root.get("id")
        manifests[addon_id] = root
        if addon_id != path.parent.name:
            errors += fail("%s: directory and add-on id differ" % path)
        if root.get("version") != expected:
            errors += fail("%s: version must be %s" % (path, expected))
        if root.find("./extension[@point='xbmc.addon.metadata']/license") is None:
            errors += fail("%s: license metadata is required" % path)
    local_ids = set(manifests)
    local_dependencies = {}
    for addon_id, root in manifests.items():
        local_dependencies[addon_id] = []
        for dependency in root.findall("./requires/import"):
            needed = dependency.get("addon")
            if needed and needed.startswith((
                    "repository.noiro", "script.module.noiro", "script.service.noiro",
                    "script.noiro", "plugin.video.noiro", "skin.noiro")) and needed not in local_ids:
                errors += fail("%s: missing local dependency %s" % (addon_id, needed))
            if needed in local_ids:
                local_dependencies[addon_id].append(needed)
    visiting = set()
    visited = set()

    def visit(addon_id, chain):
        nonlocal errors
        if addon_id in visiting:
            errors += fail("local add-on dependency cycle: %s" % " -> ".join(chain + [addon_id]))
            return
        if addon_id in visited:
            return
        visiting.add(addon_id)
        for needed in local_dependencies.get(addon_id, []):
            visit(needed, chain + [addon_id])
        visiting.remove(addon_id)
        visited.add(addon_id)

    for addon_id in sorted(local_ids):
        visit(addon_id, [])
    for path in sorted((ROOT / "addons").rglob("*.xml")):
        try:
            ET.parse(path)
        except ET.ParseError as error:
            errors += fail("%s: %s" % (path, error))
    skin_root = ROOT / "addons" / "skin.noiro" / "1080i"
    for name in REQUIRED_SKIN_WINDOWS:
        if not (skin_root / name).is_file():
            errors += fail("skin.noiro: required Kodi window is missing: %s" % name)
    for path in sorted((ROOT / "addons").rglob("*.py")) + sorted((ROOT / "scripts").glob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 9))
            py_compile.compile(str(path), doraise=True)
        except (SyntaxError, py_compile.PyCompileError) as error:
            errors += fail(str(error))
    if errors:
        return 1
    print("Validated %d Kodi add-ons and all Python/XML sources" % len(manifests))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
