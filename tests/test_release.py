import io
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons/repository.noiro/resources/lib"))

from noiro_repo.installer import InstallError, TransactionalInstaller  # noqa: E402
from noiro_repo.bootstrap import BootstrapServer  # noqa: E402
from noiro_repo.github import GitHubReleaseClient  # noqa: E402


def addon_zip(addon_id, marker, unsafe=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        path = "../escape" if unsafe else "%s/addon.xml" % addon_id
        archive.writestr(path, "<addon id='%s' version='0.2.0'/>" % addon_id)
        if not unsafe:
            archive.writestr("%s/marker.txt" % addon_id, marker)
    return output.getvalue()


class FakeRelease(object):
    def __init__(self, payloads):
        self.payloads = payloads
        self.manifest = {
            "version": "0.2.0",
            "artifacts": [
                {"name": "%s-0.2.0.zip" % addon_id, "addon_id": addon_id, "kind": "kodi-addon"}
                for addon_id in payloads
            ],
        }

    def load_latest(self, force=False):
        return self.manifest

    def asset(self, name):
        addon_id = name.rsplit("-0.2.0.zip", 1)[0]
        return self.payloads[addon_id]


class FakeResponse(object):
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class PublicRepositoryTests(unittest.TestCase):
    def test_public_repository_request_has_no_authorization_header(self):
        captured = []

        def opener(request, timeout=None):
            captured.append(request)
            return FakeResponse(json.dumps({"ok": True}).encode("utf-8"))

        client = GitHubReleaseClient(None, "/tmp/noiro-test-cache", "/tmp/noiro-public-key", opener=opener)
        client._request(client.LATEST + "/release-manifest.json")
        self.assertIsNone(captured[0].get_header("Authorization"))

    def test_repository_identity_comes_from_signed_manifest(self):
        client = GitHubReleaseClient(None, "/tmp/noiro-test-cache", "/tmp/noiro-public-key")
        client.load_latest = lambda force=False: {"product": "Noiro-Kodi"}
        self.assertTrue(client.validate_repository())

    def test_bootstrap_form_does_not_request_github_token(self):
        form = BootstrapServer.form()
        self.assertNotIn("github_token", form)
        self.assertNotIn("fine-grained", form)
        self.assertIn("Maintenance PIN", form)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addons = os.path.join(self.temp.name, "addons")
        self.data = os.path.join(self.temp.name, "data")
        self.state = os.path.join(self.temp.name, "service", "state.json")
        self.existing_addon = "script.noiro.setup"
        os.makedirs(os.path.join(self.addons, self.existing_addon), exist_ok=True)
        Path(self.addons, self.existing_addon, "addon.xml").write_text(
            "<addon id='%s' version='0.1.0'/>" % self.existing_addon,
            encoding="utf-8",
        )
        Path(self.addons, self.existing_addon, "marker.txt").write_text("old", encoding="utf-8")
        self.installer = TransactionalInstaller(self.addons, self.data, self.state)

    def tearDown(self):
        self.temp.cleanup()

    def test_update_marks_pending_and_rollback_restores_previous(self):
        version = self.installer.install(
            FakeRelease({self.existing_addon: addon_zip(self.existing_addon, "new")}),
            "0.1.0",
        )
        self.assertEqual(version, "0.2.0")
        self.assertEqual(Path(self.addons, self.existing_addon, "marker.txt").read_text(), "new")
        state = json.loads(Path(self.state).read_text())
        pending = state["boot_pending"]
        restored = self.installer.rollback(pending["backup"])
        self.assertEqual(restored, "0.1.0")
        self.assertEqual(Path(self.addons, self.existing_addon, "marker.txt").read_text(), "old")
        self.assertTrue(json.loads(Path(self.state).read_text())["maintenance_mode"])

    def test_zip_slip_is_rejected(self):
        with self.assertRaises(InstallError):
            self.installer.install(
                FakeRelease({self.existing_addon: addon_zip(self.existing_addon, "bad", unsafe=True)}),
                "0.1.0",
            )

    def test_module_and_service_packages_are_allowed_in_signed_release(self):
        payloads = {
            "script.module.noiro": addon_zip("script.module.noiro", "module"),
            "script.service.noiro": addon_zip("script.service.noiro", "service"),
        }
        self.installer.install(FakeRelease(payloads), "0.1.0")
        self.assertTrue(Path(self.addons, "script.module.noiro", "addon.xml").is_file())
        self.assertTrue(Path(self.addons, "script.service.noiro", "addon.xml").is_file())

    def test_rollback_removes_addon_that_did_not_exist_before_update(self):
        addon_id = "script.module.noiro"
        self.installer.install(FakeRelease({addon_id: addon_zip(addon_id, "new")}), "0.1.0")
        state = json.loads(Path(self.state).read_text())
        self.assertTrue(Path(self.addons, addon_id).is_dir())
        self.installer.rollback(state["boot_pending"]["backup"])
        self.assertFalse(Path(self.addons, addon_id).exists())

    def test_signed_zip_manifest_must_match_release_identity(self):
        payload = addon_zip("script.module.noiro", "new")
        release = FakeRelease({"script.service.noiro": payload})
        with self.assertRaises(InstallError):
            self.installer.install(release, "0.1.0")

    def test_near_prefix_addon_is_rejected(self):
        addon_id = "skin.noiro.untrusted"
        with self.assertRaises(InstallError):
            self.installer.install(FakeRelease({addon_id: addon_zip(addon_id, "bad")}), "0.1.0")


if __name__ == "__main__":
    unittest.main()
