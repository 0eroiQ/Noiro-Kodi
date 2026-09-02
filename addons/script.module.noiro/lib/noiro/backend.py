import json
import os
import threading
import time
import uuid

from .paths import engine_socket_path, service_data
from .pro import EntitlementStore, ProClient, ProError, validate_base_url
from .profiles import ProfileStore
from .rpc import JsonRpcClient, RpcError
from .security import SecretStore, _atomic_json_write, hash_pin, verify_pin
from .state import StateStore
from .stremio import LinkPending, StremioClient
from .subtitles import SubtitleCache, SubtitleError
from .translation import GeminiTranslator


class NoiroBackend(object):
    def __init__(self, data_dir=None, stremio=None, pro=None, pro_public_key_path=None, pro_verifier=None):
        self.data_dir = data_dir or service_data()
        os.makedirs(self.data_dir, mode=0o700, exist_ok=True)
        self.secrets = SecretStore(os.path.join(self.data_dir, "secrets.json"))
        self.profiles = ProfileStore(os.path.join(self.data_dir, "profiles.json"), self.secrets)
        self.state = StateStore(os.path.join(self.data_dir, "state.json"))
        self.stremio = stremio or StremioClient()
        self.pro_override = pro
        self.pro_device_id = self.secrets.get("pro_device_id")
        if not self.pro_device_id:
            self.pro_device_id = str(uuid.uuid4())
            self.secrets.set("pro_device_id", self.pro_device_id)
        module_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        entitlement_args = [
            os.path.join(self.data_dir, "pro-entitlement.json"),
            pro_public_key_path or os.path.join(module_root, "resources", "pro_public_key.json"),
            self.pro_device_id,
        ]
        if pro_verifier is not None:
            entitlement_args.append(pro_verifier)
        self.pro_entitlements = EntitlementStore(*entitlement_args)
        self.pro_link_session = None
        self.link_sessions = {}
        self.subtitle_cache = SubtitleCache(os.path.join(self.data_dir, "subtitles"))
        self.subtitle_jobs = {}
        self.subtitle_jobs_lock = threading.Lock()
        # Kodi asks for several Home shelves at the same time. Keep one short,
        # profile-scoped catalog snapshot so those shelves do not repeat every
        # Stremio add-on request independently during one screen paint.
        self.catalog_cache = {}
        self.catalog_cache_lock = threading.Lock()

    def _profile_and_key(self, params):
        profile_id = params.get("profile_id") or self.profiles.active_id()
        profile = self.profiles.get(profile_id)
        if not profile:
            raise ValueError("Select a Noiro profile first")
        auth_key = self.profiles.auth_key(profile_id)
        if not auth_key:
            raise ValueError("This profile is not linked to Stremio")
        return profile, auth_key

    def _addons(self, auth_key):
        return self.stremio.addons(auth_key)

    def _backup_addons(self, profile_id, addons):
        directory = os.path.join(self.data_dir, "addon-rosters")
        os.makedirs(directory, mode=0o700, exist_ok=True)
        path = os.path.join(
            directory,
            "%s-%d-%s.json" % (profile_id, int(time.time()), uuid.uuid4().hex[:8]),
        )
        _atomic_json_write(path, {"profile_id": profile_id, "addons": addons})
        backups = sorted(name for name in os.listdir(directory) if name.startswith(profile_id + "-"))
        for old in backups[:-5]:
            try:
                os.unlink(os.path.join(directory, old))
            except OSError:
                pass

    def _native_health(self):
        path = engine_socket_path()
        if not os.path.exists(path):
            return {"ready": False, "optional": True, "reason": "native engine not running"}
        try:
            value = JsonRpcClient(path, timeout=2).call("system.health")
            return dict(value or {}, ready=True)
        except RpcError as error:
            return {"ready": False, "optional": True, "reason": str(error)}

    def dispatch(self, method, params):
        handlers = {
            "system.health": self.system_health,
            "system.status": self.system_status,
            "system.set_maintenance": self.system_set_maintenance,
            "system.set_enabled": self.system_set_enabled,
            "system.set_maintenance_pin": self.system_set_maintenance_pin,
            "system.verify_maintenance_pin": self.system_verify_maintenance_pin,
            "system.confirm_boot": self.system_confirm_boot,
            "pro.status": self.pro_status,
            "pro.configure": self.pro_configure,
            "pro.link.create": self.pro_link_create,
            "pro.link.poll": self.pro_link_poll,
            "pro.refresh": self.pro_refresh,
            "pro.logout": self.pro_logout,
            "profiles.list": self.profiles_list,
            "profiles.create": self.profiles_create,
            "profiles.activate": self.profiles_activate,
            "profiles.delete": self.profiles_delete,
            "profiles.update": self.profiles_update,
            "profiles.unlock": self.profiles_unlock,
            "stremio.link.create": self.link_create,
            "stremio.link.poll": self.link_poll,
            "stremio.link.reset": self.link_reset,
            "stremio.catalogs": self.stremio_catalogs,
            "stremio.search": self.stremio_search,
            "stremio.metadata": self.stremio_metadata,
            "stremio.streams": self.stremio_streams,
            "stremio.subtitles": self.stremio_subtitles,
            "stremio.library": self.stremio_library,
            "stremio.continue": self.stremio_continue,
            "stremio.progress": self.stremio_progress,
            "stremio.addons.list": self.stremio_addons_list,
            "stremio.addons.install": self.stremio_addons_install,
            "stremio.addons.remove": self.stremio_addons_remove,
            "stremio.addons.backups": self.stremio_addons_backups,
            "stremio.addons.restore": self.stremio_addons_restore,
            "subtitle.translate": self.subtitle_translate,
            "subtitle.prepare": self.subtitle_prepare,
            "subtitle.status": self.subtitle_status,
        }
        handler = handlers.get(method)
        if not handler:
            raise ValueError("Unknown Noiro method: %s" % method)
        return handler(params)

    def system_health(self, _params):
        state = self.state.read()
        profile_store = self.profiles.health()
        return {
            "ready": profile_store,
            "protocol": 1,
            "service": "0.3.3",
            "profile_store": profile_store,
            "profile_count": len(self.profiles.list()),
            "maintenance_mode": bool(state.get("maintenance_mode")),
            "native": self._native_health(),
        }

    def system_status(self, _params):
        value = self.state.read()
        value["active_profile"] = self.profiles.active_id()
        return value

    def system_set_maintenance(self, params):
        return self.state.update(maintenance_mode=bool(params.get("enabled")))

    def system_set_enabled(self, params):
        return self.state.update(noiro_enabled=bool(params.get("enabled")))

    def system_set_maintenance_pin(self, params):
        self.secrets.set("maintenance_pin_hash", hash_pin(params.get("pin")))
        return True

    def system_verify_maintenance_pin(self, params):
        encoded = self.secrets.get("maintenance_pin_hash")
        return bool(encoded and verify_pin(params.get("pin"), encoded))

    def system_confirm_boot(self, _params):
        return self.state.confirm_boot()

    def _pro_client(self, access_token=None):
        if self.pro_override is not None:
            if access_token is not None:
                self.pro_override.access_token = access_token
            return self.pro_override
        endpoint = self.secrets.get("pro_base_url")
        if not endpoint:
            raise ProError("Noiro Pro is not configured yet")
        return ProClient(endpoint, access_token=access_token)

    def _free_pro_status(self, reason=None):
        return {
            "configured": bool(self.pro_override is not None or self.secrets.get("pro_base_url")),
            "linked": bool(self.secrets.get("pro_access_token")),
            "device_id": self.pro_device_id,
            "plan": "free",
            "pro": False,
            "features": [],
            "account_id": None,
            "expires_at": None,
            "reason": reason,
        }

    def pro_status(self, _params):
        status = self._free_pro_status()
        try:
            entitlement = self.pro_entitlements.read()
        except ProError as error:
            status["reason"] = str(error)
            return status
        if not entitlement:
            return status
        status.update(entitlement)
        status["configured"] = True
        status["linked"] = bool(self.secrets.get("pro_access_token"))
        status["reason"] = None
        return status

    def pro_configure(self, params):
        endpoint = validate_base_url(params.get("base_url"))
        previous = self.secrets.get("pro_base_url")
        if previous and previous != endpoint:
            self.secrets.set("pro_access_token", None)
            self.pro_entitlements.clear()
            self.pro_link_session = None
        self.secrets.set("pro_base_url", endpoint)
        status = self._free_pro_status()
        status["base_url"] = endpoint
        return status

    def pro_link_create(self, _params):
        session = self._pro_client().create_link(self.pro_device_id)
        self.pro_link_session = session
        return session

    def pro_link_poll(self, _params):
        session = self.pro_link_session
        if not session:
            raise ProError("No Noiro account link is active")
        if time.time() >= float(session.get("expires_at") or 0):
            self.pro_link_session = None
            return {"status": "expired"}
        result = self._pro_client().poll_link(session["device_code"])
        status = result.get("status")
        if status == "pending":
            return {
                "status": "pending",
                "code": int(result.get("code") or 101),
                "retry_after": int(session.get("poll_interval") or 2),
            }
        if status == "expired":
            self.pro_link_session = None
            return {"status": "expired"}
        if status != "linked" or not result.get("access_token"):
            raise ProError("Noiro account service returned an invalid link result")
        token = result["access_token"]
        envelope = self._pro_client(token).entitlement()
        entitlement = self.pro_entitlements.save(envelope)
        self.secrets.set("pro_access_token", token)
        self.pro_link_session = None
        return {"status": "linked", "entitlement": entitlement}

    def pro_refresh(self, _params):
        token = self.secrets.get("pro_access_token")
        if not token:
            raise ProError("This Vero is not linked to a Noiro account")
        envelope = self._pro_client(token).entitlement()
        entitlement = self.pro_entitlements.save(envelope)
        return dict(entitlement, configured=True, linked=True, reason=None)

    def pro_logout(self, _params):
        self.secrets.set("pro_access_token", None)
        self.pro_entitlements.clear()
        self.pro_link_session = None
        return self._free_pro_status()

    def profiles_list(self, _params):
        active = self.profiles.active_id()
        return [{**item.public_dict(), "active": item.id == active, "linked": bool(self.profiles.auth_key(item.id))}
                for item in self.profiles.list()]

    def profiles_create(self, params):
        value = self.profiles.create(
            params.get("name"),
            pin=params.get("pin"),
            avatar=params.get("avatar") or "default",
            target_language=params.get("target_language") or "hr",
        )
        return value.public_dict()

    def profiles_activate(self, params):
        self.profiles.activate(params.get("profile_id"))
        return True

    def profiles_delete(self, params):
        self.profiles.delete(params.get("profile_id"))
        return True

    def profiles_update(self, params):
        profile_id = params.pop("profile_id", None)
        return self.profiles.update(profile_id, **params).public_dict()

    def profiles_unlock(self, params):
        return self.profiles.unlock(params.get("profile_id"), params.get("pin"))

    def link_create(self, params):
        profile_id = params.get("profile_id")
        if not self.profiles.get(profile_id):
            raise ValueError("Unknown profile")
        session = self.stremio.create_link()
        self.link_sessions[profile_id] = session
        return {
            "code": session.code,
            "link": session.link,
            "qrcode": session.qrcode,
            "expires_at": session.expires_at,
            "poll_interval": 2,
        }

    def link_poll(self, params):
        profile_id = params.get("profile_id")
        session = self.link_sessions.get(profile_id)
        if not session:
            raise ValueError("No active link session")
        if time.time() >= session.expires_at:
            self.link_sessions.pop(profile_id, None)
            return {"status": "expired"}
        try:
            token = self.stremio.read_link(session.code)
        except LinkPending:
            return {"status": "pending", "code": 101, "retry_after": 2}
        user = self.stremio.validate(token)
        self.profiles.set_auth_key(profile_id, token)
        self.profiles.update(profile_id, email=user.get("email"))
        self._clear_catalog_cache(profile_id)
        self.link_sessions.pop(profile_id, None)
        return {"status": "linked", "user": {"email": user.get("email"), "_id": user.get("_id")}}

    def link_reset(self, params):
        profile_id = params.get("profile_id") or self.profiles.active_id()
        self.profiles.clear_auth_key(profile_id)
        self.link_sessions.pop(profile_id, None)
        self._clear_catalog_cache(profile_id)
        return True

    def stremio_catalogs(self, params):
        profile, key = self._profile_and_key(params)
        extras = params.get("extras")
        cache_key = (profile.id, json.dumps(extras or {}, sort_keys=True))
        now = time.time()
        with self.catalog_cache_lock:
            cached = self.catalog_cache.get(cache_key)
            if cached and now - cached[0] < 90:
                return cached[1]
            rows = self.stremio.catalogs(self._addons(key), extras)
            self.catalog_cache[cache_key] = (now, rows)
            return rows

    def _clear_catalog_cache(self, profile_id=None):
        with self.catalog_cache_lock:
            if profile_id is None:
                self.catalog_cache.clear()
            else:
                self.catalog_cache = {
                    key: value for key, value in self.catalog_cache.items()
                    if key[0] != profile_id
                }

    def stremio_search(self, params):
        _profile, key = self._profile_and_key(params)
        return self.stremio.search(self._addons(key), params.get("query"))

    def stremio_metadata(self, params):
        _profile, key = self._profile_and_key(params)
        return self.stremio.metadata(self._addons(key), params.get("type"), params.get("id"))

    def stremio_streams(self, params):
        _profile, key = self._profile_and_key(params)
        return self.stremio.streams(self._addons(key), params.get("type"), params.get("video_id"))

    def stremio_subtitles(self, params):
        _profile, key = self._profile_and_key(params)
        return self.stremio.subtitles(self._addons(key), params.get("type"), params.get("video_id"))

    def stremio_library(self, params):
        _profile, key = self._profile_and_key(params)
        return self.stremio.library(key)

    def stremio_continue(self, params):
        _profile, key = self._profile_and_key(params)
        results = []
        for item in self.stremio.library(key):
            if item.get("removed"):
                continue
            state = item.get("state") or {}
            position = int(state.get("timeOffset") or 0)
            duration = int(state.get("duration") or 0)
            if position <= 0 or duration <= 0 or position >= max(0, duration - 60000):
                continue
            value = dict(item)
            value["resume_position_ms"] = position
            value["resume_duration_ms"] = duration
            results.append(value)
        return sorted(results, key=lambda item: str((item.get("state") or {}).get("lastWatched") or ""), reverse=True)

    def stremio_progress(self, params):
        _profile, key = self._profile_and_key(params)
        return self.stremio.save_progress(
            key,
            params.get("meta") or {},
            float(params.get("position") or 0),
            float(params.get("duration") or 0),
        )

    def stremio_addons_list(self, params):
        _profile, key = self._profile_and_key(params)
        return self._addons(key)

    def stremio_addons_install(self, params):
        if params.get("confirmed") is not True:
            raise ValueError("Add-on installation requires confirmation")
        profile, key = self._profile_and_key(params)
        current = self._addons(key)
        descriptor = self.stremio.manifest(params.get("manifest_url"))
        addon_id = descriptor["manifest"]["id"]
        self._backup_addons(profile.id, current)
        updated = [item for item in current if (item.get("manifest") or {}).get("id") != addon_id]
        updated.append(descriptor)
        self.stremio.set_addons(key, updated)
        self._clear_catalog_cache(profile.id)
        return updated

    def stremio_addons_remove(self, params):
        if params.get("confirmed") is not True:
            raise ValueError("Add-on removal requires confirmation")
        profile, key = self._profile_and_key(params)
        current = self._addons(key)
        self._backup_addons(profile.id, current)
        updated = [item for item in current if (item.get("manifest") or {}).get("id") != params.get("addon_id")]
        self.stremio.set_addons(key, updated)
        self._clear_catalog_cache(profile.id)
        return updated

    def stremio_addons_backups(self, params):
        profile, _key = self._profile_and_key(params)
        directory = os.path.join(self.data_dir, "addon-rosters")
        if not os.path.isdir(directory):
            return []
        results = []
        for name in sorted(os.listdir(directory), reverse=True):
            if not name.startswith(profile.id + "-") or not name.endswith(".json"):
                continue
            path = os.path.join(directory, name)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                addons = list(payload.get("addons") or [])
                results.append({
                    "id": name,
                    "created_at": int(os.path.getmtime(path)),
                    "addon_count": len(addons),
                    "names": [(item.get("manifest") or {}).get("name") for item in addons],
                })
            except (OSError, ValueError):
                continue
        return results

    def stremio_addons_restore(self, params):
        if params.get("confirmed") is not True:
            raise ValueError("Add-on roster restore requires confirmation")
        profile, key = self._profile_and_key(params)
        backup_id = str(params.get("backup_id") or "")
        if os.path.basename(backup_id) != backup_id or not backup_id.startswith(profile.id + "-"):
            raise ValueError("Invalid add-on roster backup")
        path = os.path.join(self.data_dir, "addon-rosters", backup_id)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            addons = list(payload.get("addons") or [])
        except (OSError, ValueError) as error:
            raise ValueError("Add-on roster backup is unavailable: %s" % error)
        current = self._addons(key)
        self._backup_addons(profile.id, current)
        self.stremio.set_addons(key, addons)
        self._clear_catalog_cache(profile.id)
        return addons

    def subtitle_translate(self, params):
        profile = self.profiles.get(params.get("profile_id") or self.profiles.active_id())
        original = params.get("text") or ""
        if not profile or not profile.auto_translate:
            return {"text": original, "translated": False, "fallback": True}
        translator = GeminiTranslator(
            self.secrets.get("gemini_api_key"),
            os.path.join(self.data_dir, "subtitle-cache.json"),
        )
        return translator.translate(original, profile.target_language)

    @staticmethod
    def _language_key(value):
        clean = str(value or "und").strip().lower().replace("_", "-")
        aliases = {"hrv": "hr", "eng": "en", "bos": "bs", "srp": "sr"}
        return aliases.get(clean, clean.split("-", 1)[0])

    def _choose_subtitle(self, candidates, target_language):
        target = self._language_key(target_language)
        for candidate in candidates:
            if self._language_key(candidate.get("lang")) == target:
                return candidate
        for candidate in candidates:
            if self._language_key(candidate.get("lang")) == "en":
                return candidate
        return candidates[0] if candidates else None

    def _run_subtitle_translation(self, job_id, source_path, destination, target_language):
        try:
            original = self.subtitle_cache.read_text(source_path)
            translator = GeminiTranslator(
                self.secrets.get("gemini_api_key"),
                os.path.join(self.data_dir, "subtitle-cache.json"),
                timeout=20,
            )
            result = translator.translate_document(original, target_language)
            if not result.get("translated"):
                raise SubtitleError("Gemini translation was unavailable; original subtitle remains active")
            self.subtitle_cache.write_text(destination, result["text"])
            update = {
                "state": "ready",
                "path": destination,
                "translated": True,
                "finished_at": time.time(),
            }
        except Exception as error:
            update = {
                "state": "failed",
                "translated": False,
                "reason": str(error),
                "finished_at": time.time(),
            }
        with self.subtitle_jobs_lock:
            if job_id in self.subtitle_jobs:
                self.subtitle_jobs[job_id].update(update)

    def subtitle_prepare(self, params):
        profile, key = self._profile_and_key(params)
        candidates = self.stremio.subtitles(
            self._addons(key),
            params.get("type"),
            params.get("video_id"),
        )
        selected = self._choose_subtitle(candidates, profile.target_language)
        if not selected:
            return {"state": "none", "path": None, "translated": False}
        original_path = self.subtitle_cache.download(selected)
        source_language = self._language_key(selected.get("lang"))
        target_language = self._language_key(profile.target_language)
        result = {
            "state": "ready",
            "path": original_path,
            "translated": False,
            "language": source_language,
        }
        if (not profile.auto_translate or source_language == target_language
                or not self.secrets.get("gemini_api_key")):
            return result
        translated_path = self.subtitle_cache.translated_path(original_path, target_language)
        if os.path.isfile(translated_path):
            result.update({"path": translated_path, "translated": True, "language": target_language})
            return result
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "state": "queued",
            "path": original_path,
            "translated": False,
            "created_at": time.time(),
        }
        with self.subtitle_jobs_lock:
            self.subtitle_jobs[job_id] = job
        thread = threading.Thread(
            target=self._run_subtitle_translation,
            args=(job_id, original_path, translated_path, target_language),
            name="noiro-subtitle-%s" % job_id[:8],
            daemon=True,
        )
        thread.start()
        return dict(job)

    def subtitle_status(self, params):
        job_id = str(params.get("job_id") or "")
        with self.subtitle_jobs_lock:
            now = time.time()
            expired = [key for key, value in self.subtitle_jobs.items()
                       if now - float(value.get("finished_at") or value.get("created_at") or now) > 3600]
            for key in expired:
                self.subtitle_jobs.pop(key, None)
            value = self.subtitle_jobs.get(job_id)
            return dict(value) if value else {"job_id": job_id, "state": "missing", "translated": False}
