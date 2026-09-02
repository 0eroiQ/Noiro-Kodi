import json
import os
import threading
import time

from .security import _atomic_json_write


DEFAULT_STATE = {
    "maintenance_mode": False,
    "noiro_enabled": False,
    "profile_picker_pending": True,
    "boot_pending": None,
    "previous_release": None,
    "failed_boots": 0,
    "last_health": None,
}


class StateStore(object):
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()

    def read(self):
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError):
                raw = {}
            value = dict(DEFAULT_STATE)
            if isinstance(raw, dict):
                value.update(raw)
            return value

    def update(self, **changes):
        with self._lock:
            value = self.read()
            value.update(changes)
            value["updated_at"] = int(time.time())
            _atomic_json_write(self.path, value)
            return value

    def begin_update(self, version, previous_version):
        return self.update(
            boot_pending={
                "version": version,
                "previous_version": previous_version,
                "started_at": int(time.time()),
            },
            failed_boots=0,
        )

    def confirm_boot(self):
        state = self.read()
        previous = state.get("boot_pending") or state.get("previous_release")
        return self.update(
            boot_pending=None,
            previous_release=previous,
            failed_boots=0,
            last_health=int(time.time()),
        )

    def fail_boot(self):
        state = self.read()
        return self.update(failed_boots=int(state.get("failed_boots") or 0) + 1)

    @staticmethod
    def compatible_kodi_major(version):
        try:
            return int(str(version).split(".", 1)[0]) == 21
        except (TypeError, ValueError):
            return False


def ensure_private_directory(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
