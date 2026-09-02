import io
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons/repository.noiro/resources/lib"))
sys.path.insert(0, str(ROOT / "addons/script.module.noiro/lib"))

from noiro_repo.installer import InstallError, TransactionalInstaller  # noqa: E402
from noiro_repo.bootstrap import BootstrapServer  # noqa: E402
from noiro_repo.github import GitHubReleaseClient  # noqa: E402
from noiro.kodi import _replace_skin_setting  # noqa: E402


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

    def test_kodi_repository_does_not_depend_on_its_own_loopback_service(self):
        root = ET.parse(ROOT / "addons/repository.noiro/addon.xml").getroot()
        directory = root.find("./extension[@point='xbmc.addon.repository']/dir")
        values = [directory.find(name).text for name in ("info", "checksum", "datadir")]
        self.assertTrue(all(value.startswith("https://github.com/0eroiQ/Noiro-Kodi/releases/latest/download/") for value in values))
        self.assertTrue(all("127.0.0.1" not in value for value in values))

    def test_bootstrap_installs_the_complete_signed_release_transactionally(self):
        service = (ROOT / "addons/repository.noiro/service.py").read_text(encoding="utf-8")
        self.assertIn("provision_signed_release()", service)
        self.assertIn("installer().install(repository_client(), current)", service)
        self.assertNotIn("InstallAddon(script.noiro.setup)", service)

    def test_skin_contains_complete_vero_compatible_base(self):
        skin = ROOT / "addons/skin.noiro"
        for name in ("AddonBrowser.xml", "Settings.xml", "Home.xml", "Timers.xml"):
            self.assertTrue((skin / "xml" / name).is_file(), name)
        self.assertTrue((skin / "media" / "Textures.xbt").is_file())
        self.assertTrue((skin / "fonts" / "NotoSans-Regular.ttf").is_file())
        active_xml = "".join(path.read_text(encoding="utf-8") for path in (skin / "xml").glob("*.xml"))
        self.assertNotIn("white.svg", active_xml)

    def test_skin_preference_update_preserves_other_kodi_settings(self):
        original = """<?xml version='1.0'?>
<settings>
  <setting id="audiooutput.channels">8</setting>
  <setting id="lookandfeel.skin">skin.estuary</setting>
  <setting id="videoscreen.screenmode">DESKTOP</setting>
</settings>
"""
        updated = _replace_skin_setting(original, "skin.noiro")
        self.assertIn('<setting id="lookandfeel.skin">skin.noiro</setting>', updated)
        self.assertIn('<setting id="audiooutput.channels">8</setting>', updated)
        self.assertIn('<setting id="videoscreen.screenmode">DESKTOP</setting>', updated)
        self.assertEqual(updated.count("skin.noiro"), 1)

    def test_skin_preference_rejects_an_unsafe_identifier(self):
        with self.assertRaises(ValueError):
            _replace_skin_setting(
                '<setting id="lookandfeel.skin">skin.estuary</setting>',
                "skin.noiro</setting><malicious>",
            )

    def test_skin_switch_confirms_before_profile_dialogs(self):
        helper = (ROOT / "addons/script.module.noiro/lib/noiro/kodi.py").read_text(encoding="utf-8")
        self.assertIn('int(window.get("id") or 0) == 10100', helper)
        self.assertIn('json_rpc("Input.Left")', helper)
        self.assertIn('json_rpc("Input.Select")', helper)
        self.assertIn("os.replace(temporary, path)", helper)


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
