import json
import os
import shutil
import stat
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

from .security import atomic_json


class InstallError(RuntimeError):
    pass


class TransactionalInstaller(object):
    ALLOWED_ADDONS = {
        "repository.noiro",
        "script.module.noiro",
        "script.service.noiro",
        "script.noiro.return",
        "script.noiro.setup",
        "plugin.video.noiro",
        "skin.noiro",
    }

    def __init__(self, addons_dir, data_dir, service_state_path):
        self.addons_dir = addons_dir
        self.data_dir = data_dir
        self.service_state_path = service_state_path
        self.backups_dir = os.path.join(data_dir, "backups")
        os.makedirs(self.backups_dir, mode=0o700, exist_ok=True)

    @staticmethod
    def _safe_extract(payload, destination, addon_id, version):
        archive_path = os.path.join(destination, ".payload.zip")
        with open(archive_path, "wb") as handle:
            handle.write(payload)
        with zipfile.ZipFile(archive_path) as archive:
            for item in archive.infolist():
                path = item.filename.replace("\\", "/")
                parts = [part for part in path.split("/") if part]
                mode = item.external_attr >> 16
                if not parts or parts[0] != addon_id or path.startswith("/") or ".." in parts or stat.S_ISLNK(mode):
                    raise InstallError("Unsafe path in %s" % addon_id)
            archive.extractall(destination)
        os.unlink(archive_path)
        root = os.path.join(destination, addon_id)
        manifest_path = os.path.join(root, "addon.xml")
        if not os.path.isfile(manifest_path):
            raise InstallError("%s does not contain addon.xml" % addon_id)
        try:
            manifest = ET.parse(manifest_path).getroot()
        except (ET.ParseError, OSError) as error:
            raise InstallError("%s contains an invalid addon.xml: %s" % (addon_id, error))
        if manifest.tag != "addon" or manifest.get("id") != addon_id or manifest.get("version") != version:
            raise InstallError("%s manifest identity or version does not match the signed release" % addon_id)
        return root

    def _state(self):
        try:
            with open(self.service_state_path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _mark_pending(self, version, previous_version, backup_name):
        state = self._state()
        # Preserve the user's current mode. An update requested while the
        # official OSMC skin is open must not silently force Noiro back on at
        # restart; Noiro's Return action performs that transition explicitly.
        state.update({
            "boot_pending": {
                "version": version,
                "previous_version": previous_version,
                "backup": backup_name,
                "started_at": int(time.time()),
            },
            "failed_boots": 0,
        })
        atomic_json(self.service_state_path, state)

    def install(self, client, current_version):
        manifest = client.load_latest(force=True)
        version = manifest.get("version")
        addon_artifacts = [item for item in manifest.get("artifacts") or [] if item.get("kind") == "kodi-addon"]
        if not version or not addon_artifacts:
            raise InstallError("Release contains no Kodi add-ons")
        backup_name = "%s-%d" % (current_version or "unknown", int(time.time()))
        backup_root = os.path.join(self.backups_dir, backup_name)
        os.makedirs(backup_root, mode=0o700)
        staged = tempfile.mkdtemp(prefix="noiro-stage-", dir=self.data_dir)
        roots = {}
        try:
            for artifact in addon_artifacts:
                addon_id = artifact.get("addon_id")
                if addon_id not in self.ALLOWED_ADDONS:
                    raise InstallError("Release contains an unexpected add-on")
                roots[addon_id] = self._safe_extract(
                    client.asset(artifact["name"]), staged, addon_id, version
                )
            previous_addons = []
            for addon_id in roots:
                existing = os.path.join(self.addons_dir, addon_id)
                if os.path.isdir(existing):
                    shutil.copytree(existing, os.path.join(backup_root, addon_id))
                    previous_addons.append(addon_id)
            atomic_json(os.path.join(backup_root, "backup.json"), {
                "version": current_version,
                "new_version": version,
                "addons": sorted(roots),
                "previous_addons": sorted(previous_addons),
            })
            replaced = []
            try:
                for addon_id, source in roots.items():
                    target = os.path.join(self.addons_dir, addon_id)
                    old = target + ".noiro-old"
                    if os.path.exists(old):
                        shutil.rmtree(old)
                    existed = os.path.isdir(target)
                    if os.path.isdir(target):
                        os.replace(target, old)
                    try:
                        os.replace(source, target)
                    except Exception:
                        if existed and os.path.isdir(old):
                            os.replace(old, target)
                        raise
                    replaced.append((target, old, existed))
                for _target, old, _existed in replaced:
                    if os.path.isdir(old):
                        shutil.rmtree(old)
            except Exception:
                for target, old, existed in reversed(replaced):
                    if os.path.isdir(target):
                        shutil.rmtree(target)
                    if existed and os.path.isdir(old):
                        os.replace(old, target)
                raise
            self._mark_pending(version, current_version, backup_name)
            self._trim_backups()
            return version
        finally:
            shutil.rmtree(staged, ignore_errors=True)

    def rollback(self, backup_name):
        if not backup_name or os.path.basename(backup_name) != backup_name:
            raise InstallError("Invalid rollback backup")
        backup_root = os.path.join(self.backups_dir, backup_name)
        try:
            with open(os.path.join(backup_root, "backup.json"), "r", encoding="utf-8") as handle:
                metadata = json.load(handle)
        except (OSError, ValueError) as error:
            raise InstallError("Rollback backup is unavailable: %s" % error)
        previous = set(
            metadata.get("previous_addons") or []
            if "previous_addons" in metadata
            else metadata.get("addons") or []
        )
        for addon_id in metadata.get("addons") or []:
            source = os.path.join(backup_root, addon_id)
            target = os.path.join(self.addons_dir, addon_id)
            if addon_id not in previous:
                shutil.rmtree(target, ignore_errors=True)
                continue
            if not os.path.isdir(source):
                raise InstallError("Rollback backup is incomplete for %s" % addon_id)
            temporary = target + ".noiro-failed"
            if os.path.isdir(temporary):
                shutil.rmtree(temporary)
            if os.path.isdir(target):
                os.replace(target, temporary)
            shutil.copytree(source, target)
            shutil.rmtree(temporary, ignore_errors=True)
        state = self._state()
        state.update({
            "maintenance_mode": True,
            "boot_pending": None,
            "previous_release": None,
            "failed_boots": 0,
        })
        atomic_json(self.service_state_path, state)
        return metadata.get("version")

    def _trim_backups(self):
        backups = sorted(
            (name for name in os.listdir(self.backups_dir) if os.path.isdir(os.path.join(self.backups_dir, name))),
            key=lambda name: os.path.getmtime(os.path.join(self.backups_dir, name)),
        )
        for old in backups[:-2]:
            shutil.rmtree(os.path.join(self.backups_dir, old), ignore_errors=True)
