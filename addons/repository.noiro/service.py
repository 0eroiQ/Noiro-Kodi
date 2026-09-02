import base64
import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import xbmc  # type: ignore
import xbmcaddon  # type: ignore
import xbmcgui  # type: ignore
import xbmcvfs  # type: ignore


ADDON = xbmcaddon.Addon("repository.noiro")
ROOT = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
DATA = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
LIB = os.path.join(ROOT, "resources", "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from noiro_repo.bootstrap import BootstrapServer  # noqa: E402
from noiro_repo.github import GitHubReleaseClient, ReleaseError  # noqa: E402
from noiro_repo.installer import InstallError, TransactionalInstaller  # noqa: E402
from noiro_repo.security import atomic_json  # noqa: E402


PUBLIC_KEY = os.path.join(ROOT, "resources", "public_key.json")
REQUIRED_ADDONS = (
    "repository.noiro",
    "script.module.noiro",
    "script.service.noiro",
    "plugin.video.noiro",
    "script.noiro.setup",
    "script.noiro.return",
    "skin.noiro",
)


def read_json(name):
    try:
        with open(os.path.join(DATA, name), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def is_configured():
    configured = read_json("configured.json")
    if configured.get("configured"):
        return True
    legacy_path = os.path.join(DATA, "credentials.json")
    if read_json("credentials.json").get("github_token"):
        atomic_json(os.path.join(DATA, "configured.json"), {
            "configured": True,
            "schema": 1,
            "migrated_from_private_repository": True,
        })
        try:
            os.unlink(legacy_path)
        except OSError:
            pass
        return True
    return False


class RepositoryProxy(object):
    def __init__(self, port=64891):
        self.port = port
        self.server = None
        self.thread = None

    def client(self):
        return GitHubReleaseClient(None, os.path.join(DATA, "cache"), PUBLIC_KEY)

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _serve(self, include_body):
                if not self.path.startswith("/repository/"):
                    self.send_error(404)
                    return
                try:
                    name, value = owner.client().repository_asset(self.path)
                    content_type = "application/xml" if name.endswith(".xml") else "application/zip"
                    if name.endswith(".sha256"):
                        content_type = "text/plain"
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(value)))
                    self.send_header(
                        "Content-SHA256",
                        base64.b64encode(hashlib.sha256(value).digest()).decode("ascii"),
                    )
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if include_body:
                        self.wfile.write(value)
                except ReleaseError:
                    self.send_error(503, "Noiro repository unavailable")

            def do_GET(self):
                self._serve(True)

            def do_HEAD(self):
                self._serve(False)

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name="noiro-repository", daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=3)


def repository_client():
    return GitHubReleaseClient(None, os.path.join(DATA, "cache"), PUBLIC_KEY)


def state_path():
    return xbmcvfs.translatePath("special://profile/addon_data/script.service.noiro/state.json")


def installer():
    return TransactionalInstaller(
        xbmcvfs.translatePath("special://home/addons"),
        DATA,
        state_path(),
    )


def installed_versions_match():
    expected = ADDON.getAddonInfo("version")
    try:
        return all(xbmcaddon.Addon(addon_id).getAddonInfo("version") == expected for addon_id in REQUIRED_ADDONS)
    except RuntimeError:
        return False


def first_setup_path():
    return os.path.join(DATA, "first-setup-pending.json")


def service_state():
    try:
        with open(state_path(), "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def provision_signed_release():
    current = ADDON.getAddonInfo("version")
    installed = installer().install(repository_client(), current)
    if not service_state().get("noiro_enabled"):
        atomic_json(first_setup_path(), {
            "version": installed,
            "requested_at": int(time.time()),
        })
    xbmcgui.Dialog().notification(
        "Noiro setup",
        "Installed the complete signed Noiro %s package" % installed,
        xbmcgui.NOTIFICATION_INFO,
        7000,
    )
    time.sleep(2)
    xbmc.executebuiltin("RestartApp")


def start_first_setup_once():
    if not os.path.isfile(first_setup_path()):
        return False
    try:
        for addon_id in REQUIRED_ADDONS:
            xbmcaddon.Addon(addon_id)
    except RuntimeError:
        return False
    service_socket = xbmcvfs.translatePath("special://profile/addon_data/script.service.noiro/noiro-v1.sock")
    if not os.path.exists(service_socket):
        return False
    xbmc.executebuiltin("RunAddon(script.noiro.setup,?action=first_setup)")
    return True


def refresh_available_update():
    manifest = repository_client().load_latest(force=True)
    current = ADDON.getAddonInfo("version")
    payload = {"current": current, "available": manifest.get("version"), "checked_at": int(time.time())}
    if manifest.get("version") == current:
        payload["up_to_date"] = True
    atomic_json(os.path.join(DATA, "available-update.json"), payload)


def process_control_requests():
    install_request = os.path.join(DATA, "install-request.json")
    rollback_request = os.path.join(DATA, "rollback-request.json")
    if os.path.isfile(rollback_request):
        try:
            with open(rollback_request, "r", encoding="utf-8") as handle:
                request = json.load(handle)
            restored = installer().rollback(request.get("backup"))
            os.unlink(rollback_request)
            xbmcgui.Dialog().notification("Noiro rollback", "Restored %s; restarting in OSMC" % restored, xbmcgui.NOTIFICATION_INFO, 7000)
            time.sleep(2)
            xbmc.executebuiltin("RestartApp")
        except (OSError, ValueError, InstallError) as error:
            xbmcgui.Dialog().notification("Noiro rollback failed", str(error), xbmcgui.NOTIFICATION_ERROR, 7000)
        return
    if os.path.isfile(install_request):
        if xbmc.Player().isPlaying():
            return
        try:
            current = ADDON.getAddonInfo("version")
            installed = installer().install(repository_client(), current)
            os.unlink(install_request)
            xbmcgui.Dialog().notification("Noiro update", "Installed %s; restarting for health check" % installed, xbmcgui.NOTIFICATION_INFO, 7000)
            time.sleep(2)
            xbmc.executebuiltin("RestartApp")
        except (OSError, ReleaseError, InstallError) as error:
            try:
                os.unlink(install_request)
            except OSError:
                pass
            xbmcgui.Dialog().notification("Noiro update failed", str(error), xbmcgui.NOTIFICATION_ERROR, 7000)


def show_setup(url):
    qr = "https://api.qrserver.com/v1/create-qr-code/?size=420x420&data="
    import urllib.parse
    window = xbmcgui.WindowDialog()
    background = xbmcgui.ControlImage(0, 0, 1920, 1080, "", colorDiffuse="FF080B12")
    title = xbmcgui.ControlLabel(180, 110, 1560, 70, "Set up NoiroTV", alignment=2)
    image = xbmcgui.ControlImage(750, 230, 420, 420, qr + urllib.parse.quote(url, safe=""))
    label = xbmcgui.ControlLabel(240, 700, 1440, 120, "Scan the QR code with your phone\nor open:\n" + url, alignment=2)
    window.addControls([background, title, image, label])
    window.show()
    return window


def run():
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    proxy = RepositoryProxy()
    proxy.start()
    bootstrap = None
    setup_window = None
    configured_event = threading.Event()
    provision_attempted = False
    first_setup_started = False
    started_at = time.time()
    if not is_configured():
        bootstrap = BootstrapServer(DATA, PUBLIC_KEY, on_configured=configured_event.set)
        bootstrap.start()
        setup_window = show_setup(bootstrap.url)
    monitor = xbmc.Monitor()
    next_release_check = 0
    try:
        while not monitor.waitForAbort(2):
            if setup_window and is_configured():
                setup_window.close()
                setup_window = None
            should_provision = configured_event.is_set() or (
                is_configured() and not installed_versions_match()
            )
            if should_provision and not provision_attempted and not xbmc.Player().isPlaying():
                provision_attempted = True
                try:
                    provision_signed_release()
                    return
                except (OSError, ReleaseError, InstallError) as error:
                    xbmcgui.Dialog().ok(
                        "Noiro setup",
                        "The signed Noiro package could not be installed. OSMC remains active.\n\n%s" % error,
                    )
            if (not first_setup_started and time.time() - started_at >= 8
                    and start_first_setup_once()):
                first_setup_started = True
            if time.time() >= next_release_check:
                try:
                    refresh_available_update()
                except ReleaseError:
                    pass
                next_release_check = time.time() + 3600
            process_control_requests()
    finally:
        if setup_window:
            setup_window.close()
        if bootstrap:
            bootstrap.stop()
        proxy.stop()


if __name__ == "__main__":
    run()
