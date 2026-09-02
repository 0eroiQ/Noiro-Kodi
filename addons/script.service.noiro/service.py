import json
import os
import subprocess
import socket
import sys
import time

import xbmc  # type: ignore
import xbmcaddon  # type: ignore
import xbmcgui  # type: ignore
import xbmcvfs  # type: ignore

from noiro.backend import NoiroBackend
from noiro.log import get_logger
from noiro.paths import engine_socket_path, repository_data, service_data, socket_path
from noiro.rpc import JsonRpcServer
from noiro.security import SecretStore


LOG = get_logger("noiro.service")


class NativeEngine(object):
    def __init__(self):
        addon = xbmcaddon.Addon("script.service.noiro")
        root = xbmcvfs.translatePath(addon.getAddonInfo("path"))
        self.binary = os.path.join(root, "resources", "bin", "linux-armhf", "noiro-engine")
        self.process = None
        self.last_start = 0

    def start(self):
        if self.process and self.process.poll() is None:
            return True
        if not os.path.isfile(self.binary):
            LOG.warning("Native engine is not bundled in this development build")
            return False
        if time.time() - self.last_start < 5:
            return False
        self.last_start = time.time()
        self.process = None
        try:
            os.chmod(self.binary, 0o755)
            self.process = subprocess.Popen(
                [
                    self.binary,
                    "--socket", engine_socket_path(),
                    "--storage", os.path.join(service_data(), "native"),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            return True
        except OSError as error:
            LOG.error("Could not start native engine: %s", error)
            return False

    def ensure_running(self):
        if self.process and self.process.poll() is None:
            return True
        if self.process:
            LOG.error("Native engine exited with status %s; restarting", self.process.returncode)
            self.process = None
        return self.start()

    def stop(self):
        if not self.process:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


class ProgressPlayer(xbmc.Player):
    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.last_save = 0
        self.last_position = 0
        self.last_duration = 0
        self.resume_applied = False

    @staticmethod
    def _meta():
        value = xbmcgui.Window(10000).getProperty("Noiro.PlaybackMeta")
        try:
            return json.loads(value) if value else None
        except ValueError:
            return None

    def tick(self):
        if self.isPlayingVideo():
            try:
                self.last_position = self.getTime()
                self.last_duration = self.getTotalTime()
            except RuntimeError:
                pass
            self.maybe_apply_subtitle()

    def maybe_apply_subtitle(self):
        window = xbmcgui.Window(10000)
        encoded = window.getProperty("Noiro.SubtitleJob")
        if not encoded:
            return
        try:
            job = json.loads(encoded)
            status = self.backend.dispatch("subtitle.status", {"job_id": job.get("job_id")})
            if status.get("state") == "ready" and status.get("translated") and status.get("path"):
                self.setSubtitles(status["path"])
                window.clearProperty("Noiro.SubtitleJob")
            elif status.get("state") in ("failed", "missing"):
                # The original subtitle was already attached to the resolved
                # ListItem, so a translation failure needs no player action.
                window.clearProperty("Noiro.SubtitleJob")
        except Exception as error:
            LOG.warning("Subtitle status check failed: %s", error)
            window.clearProperty("Noiro.SubtitleJob")

    def save(self):
        meta = self._meta()
        if not meta:
            return
        try:
            self.tick()
            self.backend.dispatch("stremio.progress", {
                "profile_id": meta.get("profile_id"),
                "meta": meta,
                "position": self.last_position,
                "duration": self.last_duration,
            })
            self.last_save = time.time()
        except Exception as error:
            LOG.warning("Progress save failed: %s", error)

    def onPlayBackPaused(self):
        self.save()

    def onAVStarted(self):
        self.resume_applied = False
        meta = self._meta() or {}
        resume = float(meta.get("resume_seconds") or 0)
        if resume > 5:
            try:
                self.seekTime(resume)
                self.resume_applied = True
            except RuntimeError as error:
                LOG.warning("Could not resume playback: %s", error)

    def onPlayBackStopped(self):
        self.save()
        xbmcgui.Window(10000).clearProperty("Noiro.PlaybackMeta")
        xbmcgui.Window(10000).clearProperty("Noiro.SubtitleJob")

    def onPlayBackEnded(self):
        self.save()
        xbmcgui.Window(10000).clearProperty("Noiro.PlaybackMeta")
        xbmcgui.Window(10000).clearProperty("Noiro.SubtitleJob")


def migrate_provisioning(backend):
    source = os.path.join(repository_data(), "provisioning.json")
    if not os.path.isfile(source):
        return
    try:
        with open(source, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("gemini_api_key"):
            backend.secrets.set("gemini_api_key", payload["gemini_api_key"])
        if payload.get("maintenance_pin_hash"):
            backend.secrets.set("maintenance_pin_hash", payload["maintenance_pin_hash"])
        os.unlink(source)
    except (OSError, ValueError) as error:
        LOG.warning("Could not migrate bootstrap provisioning: %s", error)


def addon_exists(addon_id):
    try:
        xbmcaddon.Addon(addon_id)
        return True
    except RuntimeError:
        return False


def kodi_major():
    value = xbmc.getInfoLabel("System.BuildVersion")
    try:
        return int(value.split(".", 1)[0])
    except (TypeError, ValueError):
        return 0


def repository_ready():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1)
    try:
        probe.connect(("127.0.0.1", 64891))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def required_addons():
    return (
        "repository.noiro",
        "script.module.noiro",
        "script.service.noiro",
        "plugin.video.noiro",
        "script.noiro.setup",
        "script.noiro.return",
        "skin.noiro",
    )


def boot_preflight(backend, monitor):
    state = backend.state.read()
    pending = state.get("boot_pending")
    missing = [addon_id for addon_id in required_addons() if not addon_exists(addon_id)]
    healthy = False
    prior_failed_boot = bool(pending and int(state.get("failed_boots") or 0) > 0)
    deadline = time.time() if prior_failed_boot else time.time() + (45 if pending else 30)
    while time.time() < deadline and not monitor.abortRequested():
        service_health = backend.system_health({})
        native = service_health.get("native") or {}
        missing = [addon_id for addon_id in required_addons() if not addon_exists(addon_id)]
        healthy = (
            kodi_major() == 21
            and not missing
            and service_health.get("ready")
            and service_health.get("profile_store")
            and native.get("ready")
            and repository_ready()
        )
        if pending:
            healthy = healthy and xbmcgui.Window(10000).getProperty("Noiro.SkinHeartbeat") == "1"
        if healthy:
            break
        monitor.waitForAbort(1)
    if pending:
        if prior_failed_boot:
            healthy = False
        if healthy:
            backend.state.confirm_boot()
        else:
            failed = backend.state.fail_boot()
            backend.state.update(maintenance_mode=True)
            try:
                from noiro.kodi import set_skin
                set_skin("skin.estuary")
            except Exception:
                pass
            if failed.get("failed_boots", 0) >= 2:
                rollback = os.path.join(repository_data(), "rollback-request.json")
                with open(rollback, "w", encoding="utf-8") as handle:
                    json.dump(pending or {}, handle)
                try:
                    os.chmod(rollback, 0o600)
                except OSError:
                    pass
    return healthy, missing


def maybe_open_profile_picker(backend, monitor):
    state = backend.state.read()
    # A pending release must finish its two-boot audit even after the first
    # failed boot deliberately switches Kodi to Estuary maintenance mode.
    # Otherwise the second boot would return early here and rollback could
    # never be requested.
    if state.get("boot_pending"):
        healthy, missing = boot_preflight(backend, monitor)
        if not healthy:
            LOG.error("Pending Noiro release failed preflight; missing=%s Kodi=%s", missing, kodi_major())
            return
        state = backend.state.read()
    if state.get("maintenance_mode") or not state.get("noiro_enabled"):
        return
    if not state.get("boot_pending"):
        healthy, missing = boot_preflight(backend, monitor)
        if not healthy:
            LOG.error("Noiro preflight failed; missing=%s Kodi=%s", missing, kodi_major())
            backend.state.update(maintenance_mode=True)
            return
    if monitor.waitForAbort(3):
        return
    xbmc.executebuiltin("RunAddon(script.noiro.setup,?action=profiles&boot=1)")


def run():
    backend = NoiroBackend()
    migrate_provisioning(backend)
    native = NativeEngine()
    native.start()
    server = JsonRpcServer(socket_path(), backend.dispatch)
    server.start()
    monitor = xbmc.Monitor()
    player = ProgressPlayer(backend)
    LOG.info("Noiro service protocol v1 started")
    maybe_open_profile_picker(backend, monitor)
    try:
        while not monitor.abortRequested():
            native.ensure_running()
            player.tick()
            if player.isPlayingVideo() and time.time() - player.last_save >= 20:
                player.save()
            if monitor.waitForAbort(1):
                break
    finally:
        server.stop()
        native.stop()
        LOG.info("Noiro service stopped")


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        LOG.exception("Noiro service failed: %s", error)
        sys.exit(1)
