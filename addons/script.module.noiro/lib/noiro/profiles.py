import json
import os
import threading
import time
import uuid

from .models import Profile
from .security import _atomic_json_write, hash_pin, verify_pin


class ProfileStore(object):
    def __init__(self, path, secrets_store):
        self.path = path
        self.secrets = secrets_store
        self._lock = threading.RLock()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else {"profiles": [], "active": None}
        except (OSError, ValueError):
            return {"profiles": [], "active": None}

    def _save(self, payload):
        _atomic_json_write(self.path, payload)

    def health(self):
        if not os.path.exists(self.path):
            return True
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            profiles = payload.get("profiles")
            if not isinstance(payload, dict) or not isinstance(profiles, list):
                return False
            identifiers = [item.get("id") for item in profiles if isinstance(item, dict)]
            if len(identifiers) != len(profiles) or any(not item for item in identifiers):
                return False
            return len(identifiers) == len(set(identifiers))
        except (OSError, ValueError, AttributeError):
            return False

    def list(self):
        with self._lock:
            return [Profile(**item) for item in self._load().get("profiles", [])]

    def create(self, name, pin=None, avatar="default", target_language="hr"):
        clean_name = str(name).strip()
        if not clean_name:
            raise ValueError("Profile name is required")
        profile = Profile(
            id=uuid.uuid4().hex,
            name=clean_name[:40],
            avatar=avatar or "default",
            pin_hash=hash_pin(pin) if pin else None,
            target_language=target_language or "hr",
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        with self._lock:
            payload = self._load()
            payload.setdefault("profiles", []).append(profile.__dict__)
            if not payload.get("active"):
                payload["active"] = profile.id
            self._save(payload)
        return profile

    def get(self, profile_id):
        for profile in self.list():
            if profile.id == profile_id:
                return profile
        return None

    def update(self, profile_id, **changes):
        allowed = {"name", "avatar", "target_language", "auto_translate", "email"}
        with self._lock:
            payload = self._load()
            for item in payload.get("profiles", []):
                if item.get("id") == profile_id:
                    for key, value in changes.items():
                        if key in allowed:
                            item[key] = value
                    self._save(payload)
                    return Profile(**item)
        raise KeyError(profile_id)

    def set_pin(self, profile_id, pin):
        with self._lock:
            payload = self._load()
            for item in payload.get("profiles", []):
                if item.get("id") == profile_id:
                    item["pin_hash"] = hash_pin(pin) if pin else None
                    self._save(payload)
                    return
        raise KeyError(profile_id)

    def unlock(self, profile_id, pin):
        profile = self.get(profile_id)
        return bool(profile and (not profile.pin_hash or verify_pin(pin, profile.pin_hash)))

    def activate(self, profile_id):
        if not self.get(profile_id):
            raise KeyError(profile_id)
        with self._lock:
            payload = self._load()
            payload["active"] = profile_id
            self._save(payload)

    def active_id(self):
        return self._load().get("active")

    def delete(self, profile_id):
        with self._lock:
            payload = self._load()
            payload["profiles"] = [item for item in payload.get("profiles", []) if item.get("id") != profile_id]
            if payload.get("active") == profile_id:
                payload["active"] = payload["profiles"][0]["id"] if payload["profiles"] else None
            self._save(payload)
            self.secrets.delete_prefix("profile:%s:" % profile_id)

    def auth_key(self, profile_id):
        return self.secrets.get("profile:%s:stremio" % profile_id)

    def set_auth_key(self, profile_id, token):
        if not self.get(profile_id):
            raise KeyError(profile_id)
        self.secrets.set("profile:%s:stremio" % profile_id, token)

    def clear_auth_key(self, profile_id):
        self.secrets.set("profile:%s:stremio" % profile_id, None)
