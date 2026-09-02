import json
import os
import sys
import tempfile
import threading
import time
import unittest
import base64
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons/script.module.noiro/lib"))

from noiro.backend import NoiroBackend  # noqa: E402
from noiro.models import LinkSession  # noqa: E402
from noiro.pro import ProClient, ProError, canonical_json, validate_base_url, verify_entitlement  # noqa: E402
from noiro.rpc import JsonRpcClient, JsonRpcServer, RpcError  # noqa: E402
from noiro.security import SecretStore, hash_pin, verify_pin  # noqa: E402
from noiro.state import StateStore  # noqa: E402
from noiro.stremio import LinkPending  # noqa: E402
from noiro.translation import GeminiTranslator  # noqa: E402


class FakeStremio(object):
    def __init__(self):
        self.link_reads = 0
        self.collections = {}
        self.progress = []
        self.catalog_calls = 0

    def create_link(self):
        return LinkSession("ABC123", "https://link.example/ABC123", "", time.time() + 300)

    def read_link(self, code):
        self.link_reads += 1
        if self.link_reads == 1:
            raise LinkPending()
        return "token-" + code

    def validate(self, token):
        return {"_id": "user-" + token, "email": token + "@example.test"}

    def addons(self, token):
        return list(self.collections.get(token, []))

    def set_addons(self, token, addons):
        self.collections[token] = list(addons)

    def manifest(self, url):
        return {"transportUrl": url, "manifest": {"id": "example.addon", "name": "Example", "resources": ["stream"]}}

    def catalogs(self, addons, extras=None):
        self.catalog_calls += 1
        return [{"catalogId": "top", "metas": [{"id": "tt1", "name": "Movie", "type": "movie"}]}]

    def search(self, addons, query):
        return [{"query": query}]

    def metadata(self, addons, content_type, item_id):
        return {"id": item_id, "type": content_type, "name": "Movie"}

    def streams(self, addons, content_type, video_id):
        return [{"url": "https://example.test/movie.mkv", "playable": True, "locked": False}]

    def subtitles(self, addons, content_type, video_id):
        return [{"id": "subtitle-1", "lang": "hr", "url": "https://example.test/movie.srt"}]

    def library(self, token):
        return [{"_id": "tt1", "name": token, "type": "movie"}]

    def save_progress(self, token, meta, position, duration):
        self.progress.append((token, meta, position, duration))
        return True


class FakePro(object):
    def __init__(self):
        self.device_id = None
        self.polls = 0
        self.access_token = None

    def create_link(self, device_id, device_name="Vero 4K+"):
        self.device_id = device_id
        return {
            "device_code": "device-code",
            "user_code": "ABCD-1234",
            "verification_uri": "https://account.example.test/activate",
            "verification_uri_complete": "https://account.example.test/activate/ABCD-1234",
            "expires_at": time.time() + 300,
            "poll_interval": 2,
        }

    def poll_link(self, _device_code):
        self.polls += 1
        if self.polls == 1:
            return {"status": "pending", "code": 101}
        return {"status": "linked", "access_token": "pro-access-token"}

    def entitlement(self):
        now = int(time.time())
        return {
            "schema": 1,
            "payload": {
                "account_id": "acct_test",
                "device_id": self.device_id,
                "expires_at": now + 3600,
                "features": ["pro_badge"],
                "issued_at": now,
                "plan": "pro",
            },
            "signature": base64.b64encode(b"test-signature").decode("ascii"),
        }


class FakeHttpResponse(object):
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fake = FakeStremio()
        self.backend = NoiroBackend(self.temp.name, stremio=self.fake)

    def tearDown(self):
        self.temp.cleanup()

    def test_pin_is_salted_and_verified(self):
        first = hash_pin("1234")
        second = hash_pin("1234")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_pin("1234", first))
        self.assertFalse(verify_pin("9999", first))

    def test_secret_file_is_private(self):
        path = os.path.join(self.temp.name, "secrets.json")
        store = SecretStore(path)
        store.set("token", "private")
        self.assertEqual(store.get("token"), "private")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_entitlement_is_device_bound_signed_and_expiring(self):
        now = 1_800_000_000
        payload = {
            "account_id": "acct_1",
            "device_id": "vero-1",
            "expires_at": now + 3600,
            "features": ["pro_badge"],
            "issued_at": now - 10,
            "plan": "pro",
        }
        expected = canonical_json(payload)
        envelope = {
            "schema": 1,
            "payload": dict(payload),
            "signature": base64.b64encode(b"valid").decode("ascii"),
        }
        verifier = lambda message, signature, _key: message == expected and signature == b"valid"
        verified = verify_entitlement(envelope, {}, "vero-1", now=now, verifier=verifier)
        self.assertTrue(verified["pro"])
        envelope["payload"]["plan"] = "free"
        with self.assertRaises(ProError):
            verify_entitlement(envelope, {}, "vero-1", now=now, verifier=verifier)
        envelope["payload"] = dict(payload)
        with self.assertRaises(ProError):
            verify_entitlement(envelope, {}, "another-vero", now=now, verifier=verifier)
        with self.assertRaises(ProError):
            verify_entitlement(envelope, {}, "vero-1", now=now + 7200, verifier=verifier)

    def test_pro_client_keeps_bearer_token_in_authorization_header(self):
        captured = []

        def opener(request, timeout=None):
            captured.append(request)
            return FakeHttpResponse({"schema": 1, "payload": {}, "signature": "x"})

        client = ProClient("https://account.example.test", access_token="secret-token", opener=opener)
        client.entitlement()
        self.assertEqual(captured[0].get_header("Authorization"), "Bearer secret-token")
        self.assertNotIn("secret-token", captured[0].full_url)
        self.assertEqual(validate_base_url("http://192.168.4.2:8098"), "http://192.168.4.2:8098")
        with self.assertRaises(ProError):
            validate_base_url("http://account.example.test")

    def test_pro_link_does_not_change_profile_stremio_token(self):
        fake_pro = FakePro()
        public_key = os.path.join(self.temp.name, "pro-public.json")
        Path(public_key).write_text("{}", encoding="utf-8")
        backend = NoiroBackend(
            self.temp.name,
            stremio=self.fake,
            pro=fake_pro,
            pro_public_key_path=public_key,
            pro_verifier=lambda _message, signature, _key: signature == b"test-signature",
        )
        profile = backend.dispatch("profiles.create", {"name": "Viewer"})
        backend.profiles.set_auth_key(profile["id"], "stremio-token")
        backend.dispatch("pro.configure", {"base_url": "https://account.example.test"})
        backend.dispatch("pro.link.create", {})
        self.assertEqual(backend.dispatch("pro.link.poll", {})["code"], 101)
        linked = backend.dispatch("pro.link.poll", {})
        self.assertEqual(linked["status"], "linked")
        self.assertTrue(backend.dispatch("pro.status", {})["pro"])
        self.assertEqual(backend.profiles.auth_key(profile["id"]), "stremio-token")
        backend.dispatch("pro.logout", {})
        self.assertFalse(backend.dispatch("pro.status", {})["pro"])
        self.assertEqual(backend.profiles.auth_key(profile["id"]), "stremio-token")

    def test_profiles_never_share_stremio_tokens(self):
        first = self.backend.dispatch("profiles.create", {"name": "Alice"})
        second = self.backend.dispatch("profiles.create", {"name": "Bob"})
        self.backend.profiles.set_auth_key(first["id"], "alice-token")
        self.backend.profiles.set_auth_key(second["id"], "bob-token")
        self.assertEqual(self.backend.profiles.auth_key(first["id"]), "alice-token")
        self.assertEqual(self.backend.profiles.auth_key(second["id"]), "bob-token")
        self.backend.dispatch("stremio.link.reset", {"profile_id": first["id"]})
        self.assertIsNone(self.backend.profiles.auth_key(first["id"]))
        self.assertEqual(self.backend.profiles.auth_key(second["id"]), "bob-token")

    def test_link_polls_pending_101_then_validates_token(self):
        profile = self.backend.dispatch("profiles.create", {"name": "Viewer"})
        session = self.backend.dispatch("stremio.link.create", {"profile_id": profile["id"]})
        self.assertEqual(session["poll_interval"], 2)
        pending = self.backend.dispatch("stremio.link.poll", {"profile_id": profile["id"]})
        self.assertEqual(pending, {"status": "pending", "code": 101, "retry_after": 2})
        linked = self.backend.dispatch("stremio.link.poll", {"profile_id": profile["id"]})
        self.assertEqual(linked["status"], "linked")
        self.assertEqual(self.backend.profiles.auth_key(profile["id"]), "token-ABC123")

    def test_addon_change_requires_confirmation_and_preserves_backup(self):
        profile = self.backend.dispatch("profiles.create", {"name": "Viewer"})
        self.backend.profiles.set_auth_key(profile["id"], "token")
        self.fake.collections["token"] = [{"transportUrl": "https://old/manifest.json", "manifest": {"id": "old", "name": "Old"}}]
        with self.assertRaises(ValueError):
            self.backend.dispatch("stremio.addons.install", {"profile_id": profile["id"], "manifest_url": "https://new/manifest.json"})
        updated = self.backend.dispatch("stremio.addons.install", {
            "profile_id": profile["id"], "manifest_url": "https://new/manifest.json", "confirmed": True
        })
        self.assertEqual(updated[-1]["manifest"]["id"], "example.addon")
        backup_dir = os.path.join(self.temp.name, "addon-rosters")
        self.assertEqual(len(os.listdir(backup_dir)), 1)

    def test_addon_roster_can_be_restored_with_confirmation(self):
        profile = self.backend.dispatch("profiles.create", {"name": "Viewer"})
        self.backend.profiles.set_auth_key(profile["id"], "token")
        old = {"transportUrl": "https://old/manifest.json", "manifest": {"id": "old", "name": "Old"}}
        self.fake.collections["token"] = [old]
        self.backend.dispatch("stremio.addons.install", {
            "profile_id": profile["id"], "manifest_url": "https://new/manifest.json", "confirmed": True
        })
        backup = self.backend.dispatch("stremio.addons.backups", {"profile_id": profile["id"]})[0]
        with self.assertRaises(ValueError):
            self.backend.dispatch("stremio.addons.restore", {
                "profile_id": profile["id"], "backup_id": backup["id"]
            })
        restored = self.backend.dispatch("stremio.addons.restore", {
            "profile_id": profile["id"], "backup_id": backup["id"], "confirmed": True
        })
        self.assertEqual(restored[0]["manifest"]["id"], "old")

    def test_subtitle_prepare_returns_original_without_blocking_on_gemini(self):
        class FakeSubtitleCache(object):
            def download(self, candidate):
                return "/tmp/noiro-original.srt"

        profile = self.backend.dispatch("profiles.create", {"name": "Viewer", "target_language": "hr"})
        self.backend.profiles.set_auth_key(profile["id"], "token")
        self.backend.subtitle_cache = FakeSubtitleCache()
        result = self.backend.dispatch("subtitle.prepare", {
            "profile_id": profile["id"], "type": "movie", "video_id": "tt1"
        })
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["path"], "/tmp/noiro-original.srt")
        self.assertFalse(result["translated"])

    def test_progress_is_scoped_to_active_profile_token(self):
        profile = self.backend.dispatch("profiles.create", {"name": "Viewer"})
        self.backend.profiles.set_auth_key(profile["id"], "viewer-token")
        self.backend.dispatch("stremio.progress", {
            "profile_id": profile["id"], "meta": {"libraryId": "tt1"}, "position": 20, "duration": 100
        })
        self.assertEqual(self.fake.progress[0][0], "viewer-token")

    def test_continue_watching_uses_only_active_profile_library(self):
        profile = self.backend.dispatch("profiles.create", {"name": "Viewer"})
        self.backend.profiles.set_auth_key(profile["id"], "viewer-token")
        self.fake.library = lambda token: [{
            "_id": "tt1", "name": token, "type": "movie", "state": {
                "timeOffset": 20000, "duration": 100000, "lastWatched": "2026-09-02T00:00:00Z"
            }
        }]
        rows = self.backend.dispatch("stremio.continue", {"profile_id": profile["id"]})
        self.assertEqual(rows[0]["name"], "viewer-token")
        self.assertEqual(rows[0]["resume_position_ms"], 20000)

    def test_home_widget_catalog_requests_share_short_profile_cache(self):
        profile = self.backend.dispatch("profiles.create", {"name": "Viewer"})
        self.backend.profiles.set_auth_key(profile["id"], "viewer-token")
        first = self.backend.dispatch("stremio.catalogs", {"profile_id": profile["id"]})
        second = self.backend.dispatch("stremio.catalogs", {"profile_id": profile["id"]})
        self.assertEqual(first, second)
        self.assertEqual(self.fake.catalog_calls, 1)

    def test_addon_change_invalidates_home_widget_catalog_cache(self):
        profile = self.backend.dispatch("profiles.create", {"name": "Viewer"})
        self.backend.profiles.set_auth_key(profile["id"], "viewer-token")
        self.backend.dispatch("stremio.catalogs", {"profile_id": profile["id"]})
        self.backend.dispatch("stremio.addons.install", {
            "profile_id": profile["id"],
            "manifest_url": "https://new/manifest.json",
            "confirmed": True,
        })
        self.backend.dispatch("stremio.catalogs", {"profile_id": profile["id"]})
        self.assertEqual(self.fake.catalog_calls, 2)

    def test_gemini_failure_is_fail_soft(self):
        def broken(_request, timeout=None):
            raise OSError("offline")
        translator = GeminiTranslator("key", os.path.join(self.temp.name, "cache.json"), opener=broken)
        result = translator.translate("Hello", "hr")
        self.assertEqual(result["text"], "Hello")
        self.assertTrue(result["fallback"])

    def test_state_keeps_maintenance_across_new_instance(self):
        path = os.path.join(self.temp.name, "state.json")
        StateStore(path).update(maintenance_mode=True)
        self.assertTrue(StateStore(path).read()["maintenance_mode"])
        self.assertTrue(StateStore.compatible_kodi_major("21.2"))
        self.assertFalse(StateStore.compatible_kodi_major("22.0"))

    def test_successful_boot_retains_manual_rollback_target(self):
        path = os.path.join(self.temp.name, "state.json")
        pending = {"version": "0.2.0", "previous_version": "0.1.0", "backup": "0.1.0-1"}
        state = StateStore(path)
        state.update(boot_pending=pending)
        confirmed = state.confirm_boot()
        self.assertIsNone(confirmed["boot_pending"])
        self.assertEqual(confirmed["previous_release"], pending)

    def test_corrupt_profile_database_fails_health_check(self):
        Path(self.temp.name, "profiles.json").write_text("not-json", encoding="utf-8")
        health = NoiroBackend(self.temp.name, stremio=self.fake).dispatch("system.health", {})
        self.assertFalse(health["ready"])
        self.assertFalse(health["profile_store"])

    def test_unix_rpc_round_trip_and_unknown_method(self):
        path = os.path.join(self.temp.name, "rpc.sock")
        server = JsonRpcServer(path, self.backend.dispatch)
        server.start()
        try:
            client = JsonRpcClient(path)
            health = client.call("system.health")
            self.assertTrue(health["ready"])
            with self.assertRaises(RpcError):
                client.call("does.not.exist")
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
