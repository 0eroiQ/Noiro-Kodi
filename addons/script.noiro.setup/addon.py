import json
import os
import sys
import time
import urllib.parse

import xbmc  # type: ignore
import xbmcaddon  # type: ignore
import xbmcgui  # type: ignore
import xbmcvfs  # type: ignore

from noiro.kodi import activate, set_skin
from noiro.paths import service_data, socket_path
from noiro.rpc import JsonRpcClient, RpcError


RPC = JsonRpcClient(socket_path())
DIALOG = xbmcgui.Dialog()


def params():
    query = next((item for item in sys.argv[1:] if item.startswith("?")), "")
    return {key: values[-1] for key, values in urllib.parse.parse_qs(query.lstrip("?")).items()}


def call(method, value=None):
    return RPC.call(method, value or {})


def notify(title, message):
    DIALOG.notification(title, message, xbmcgui.NOTIFICATION_INFO, 5000)


def maintenance_pin():
    pin = DIALOG.numeric(0, "Maintenance PIN")
    if not pin or not call("system.verify_maintenance_pin", {"pin": pin}):
        DIALOG.ok("Noiro Maintenance", "The Maintenance PIN is incorrect.")
        return False
    return True


class LinkWindow(xbmcgui.WindowDialog):
    def __init__(self, session):
        super().__init__()
        self.cancelled = False
        background = xbmcgui.ControlImage(0, 0, 1920, 1080, "", colorDiffuse="FF080B12")
        title = xbmcgui.ControlLabel(240, 90, 1440, 70, "Link Stremio account", alignment=2)
        qr_value = session.get("qrcode") or session.get("link")
        if not str(qr_value).startswith(("http://", "https://")):
            qr_value = "https://api.qrserver.com/v1/create-qr-code/?size=440x440&data=" + urllib.parse.quote(session.get("link") or "", safe="")
        image = xbmcgui.ControlImage(740, 210, 440, 440, qr_value)
        label = xbmcgui.ControlLabel(
            260, 690, 1400, 180,
            "Scan the QR code or open\n%s\n\nCode expires in five minutes" % (session.get("link") or "link.stremio.com"),
            alignment=2,
        )
        self.addControls([background, title, image, label])

    def onAction(self, action):
        if action.getId() in (9, 10, 92, 216, 247):
            self.cancelled = True
            self.close()


class ProLinkWindow(xbmcgui.WindowDialog):
    def __init__(self, session):
        super().__init__()
        self.cancelled = False
        background = xbmcgui.ControlImage(0, 0, 1920, 1080, "", colorDiffuse="FF080B12")
        title = xbmcgui.ControlLabel(240, 90, 1440, 70, "Connect Noiro account", alignment=2)
        controls = [background, title]
        qr_value = session.get("qrcode")
        if str(qr_value).startswith(("http://", "https://")):
            controls.append(xbmcgui.ControlImage(740, 210, 440, 440, qr_value))
            top = 690
        else:
            top = 330
        link = session.get("verification_uri") or ""
        code = session.get("user_code") or ""
        controls.append(xbmcgui.ControlLabel(
            260, top, 1400, 260,
            "Open on your phone:\n%s\n\nCode: %s\n\nThis code expires in five minutes" % (link, code),
            alignment=2,
        ))
        self.addControls(controls)

    def onAction(self, action):
        if action.getId() in (9, 10, 92, 216, 247):
            self.cancelled = True
            self.close()


def link_profile(profile_id):
    try:
        session = call("stremio.link.create", {"profile_id": profile_id})
    except RpcError as error:
        DIALOG.ok("Stremio", str(error))
        return False
    window = LinkWindow(session)
    window.show()
    monitor = xbmc.Monitor()
    try:
        deadline = min(float(session.get("expires_at") or 0), time.time() + 300)
        while time.time() < deadline and not monitor.abortRequested() and not window.cancelled:
            if monitor.waitForAbort(2):
                break
            result = call("stremio.link.poll", {"profile_id": profile_id})
            if result.get("status") == "linked":
                window.close()
                notify("Stremio", "Account linked to this Noiro profile")
                return True
            if result.get("status") == "expired":
                break
    except RpcError as error:
        window.close()
        DIALOG.ok("Stremio", str(error))
        return False
    window.close()
    if not window.cancelled:
        DIALOG.ok("Stremio", "The link code expired. Request a new code to try again.")
    return False


def configure_pro():
    if not maintenance_pin():
        return False
    address = DIALOG.input(
        "Noiro account service address",
        type=xbmcgui.INPUT_ALPHANUM,
    ).strip()
    if not address:
        return False
    try:
        call("pro.configure", {"base_url": address})
        notify("Noiro Pro", "Account service saved")
        return True
    except RpcError as error:
        DIALOG.ok("Noiro Pro", str(error))
        return False


def link_pro():
    status = call("pro.status")
    if not status.get("configured") and not configure_pro():
        return False
    try:
        session = call("pro.link.create")
    except RpcError as error:
        DIALOG.ok("Noiro Pro", str(error))
        return False
    window = ProLinkWindow(session)
    window.show()
    monitor = xbmc.Monitor()
    interval = max(2, int(session.get("poll_interval") or 2))
    try:
        deadline = min(float(session.get("expires_at") or 0), time.time() + 300)
        while time.time() < deadline and not monitor.abortRequested() and not window.cancelled:
            if monitor.waitForAbort(interval):
                break
            result = call("pro.link.poll")
            if result.get("status") == "linked":
                window.close()
                entitlement = result.get("entitlement") or {}
                plan = str(entitlement.get("plan") or "free").upper()
                DIALOG.ok("Noiro Pro", "Noiro account connected.\n\nPlan: %s" % plan)
                return True
            if result.get("status") == "expired":
                break
    except RpcError as error:
        window.close()
        DIALOG.ok("Noiro Pro", str(error))
        return False
    window.close()
    if not window.cancelled:
        DIALOG.ok("Noiro Pro", "The account-link code expired. Request a new code to try again.")
    return False


def pro_menu():
    status = call("pro.status")
    plan = "PRO" if status.get("pro") else "FREE"
    options = ["Status · %s" % plan]
    actions = ["status"]
    if status.get("linked"):
        options.extend(["Refresh membership", "Disconnect Noiro account"])
        actions.extend(["refresh", "logout"])
    else:
        options.append("Connect Noiro account")
        actions.append("link")
    options.append("Set account service address")
    actions.append("configure")
    selected = DIALOG.select("Noiro Pro", options)
    if selected < 0:
        return
    action = actions[selected]
    if action == "status":
        features = status.get("features") or []
        detail = "Plan: %s\nAccount: %s\nValid until: %s" % (
            plan,
            status.get("account_id") or "Not connected",
            time.strftime("%Y-%m-%d %H:%M", time.localtime(status.get("expires_at")))
            if status.get("expires_at") else "—",
        )
        if features:
            detail += "\n\nEnabled: " + ", ".join(features)
        if status.get("reason"):
            detail += "\n\n" + str(status["reason"])
        DIALOG.ok("Noiro Pro", detail)
    elif action == "link":
        link_pro()
    elif action == "refresh":
        try:
            refreshed = call("pro.refresh")
            DIALOG.ok("Noiro Pro", "Membership refreshed.\n\nPlan: %s" % str(refreshed.get("plan") or "free").upper())
        except RpcError as error:
            DIALOG.ok("Noiro Pro", str(error))
    elif action == "logout":
        if DIALOG.yesno("Noiro Pro", "Disconnect this Vero from the Noiro account?"):
            call("pro.logout")
            notify("Noiro Pro", "Account disconnected; playback and Free features still work")
    elif action == "configure":
        configure_pro()


def create_profile():
    name = DIALOG.input("Profile name", type=xbmcgui.INPUT_ALPHANUM)
    if not name.strip():
        return None
    add_pin = DIALOG.yesno("Profile PIN", "Protect this profile with a four-digit PIN?")
    pin = DIALOG.numeric(0, "Four-digit profile PIN") if add_pin else None
    try:
        profile = call("profiles.create", {"name": name, "pin": pin or None})
    except RpcError as error:
        DIALOG.ok("Noiro profile", str(error))
        return None
    if DIALOG.yesno("Stremio", "Link a Stremio account to %s now?" % profile["name"]):
        link_profile(profile["id"])
    return profile


def select_profile(boot=False):
    profiles = call("profiles.list")
    if not profiles:
        created = create_profile()
        profiles = call("profiles.list") if created else []
    labels = [
        ("🔒 " if item.get("locked") else "") + item["name"] + ("  • Link Stremio" if not item.get("linked") else "")
        for item in profiles
    ]
    labels.append("＋ Add profile")
    selected = DIALOG.select("Who is watching?", labels)
    if selected < 0:
        return
    if selected == len(profiles):
        profile = create_profile()
        if profile:
            select_profile(boot=boot)
        return
    profile = profiles[selected]
    if profile.get("locked"):
        pin = DIALOG.numeric(0, "PIN for %s" % profile["name"])
        if not call("profiles.unlock", {"profile_id": profile["id"], "pin": pin}):
            DIALOG.ok("Noiro", "Incorrect profile PIN")
            return
    call("profiles.activate", {"profile_id": profile["id"]})
    if not profile.get("linked"):
        if not link_profile(profile["id"]):
            return
    activate("videos", "plugin://plugin.video.noiro/")


def open_osmc():
    if not maintenance_pin():
        return
    call("system.set_maintenance", {"enabled": True})
    set_skin("skin.estuary")
    notify("Noiro Maintenance", "Estuary will remain active through restart and reboot")
    activate("home")


def return_noiro():
    try:
        health = call("system.health")
        major = int(xbmc.getInfoLabel("System.BuildVersion").split(".", 1)[0])
        for addon_id in (
            "repository.noiro",
            "script.module.noiro",
            "script.service.noiro",
            "plugin.video.noiro",
            "script.noiro.setup",
            "script.noiro.return",
            "skin.noiro",
        ):
            xbmcaddon.Addon(addon_id)
        if major != 21 or not health.get("ready") or not (health.get("native") or {}).get("ready"):
            raise RuntimeError("This Noiro release is not compatible with the installed Kodi version")
        call("system.set_maintenance", {"enabled": False})
        call("system.set_enabled", {"enabled": True})
        set_skin("skin.noiro")
        select_profile(boot=True)
    except (RuntimeError, RpcError) as error:
        DIALOG.ok("Return to Noiro", "%s\n\nEstuary will remain active." % error)


def show_logs():
    path = xbmcvfs.translatePath("special://logpath/kodi.log")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = [line for line in handle.readlines()[-2500:] if "noiro" in line.lower()]
        DIALOG.textviewer("Noiro Logs", "".join(lines[-500:]) or "No Noiro log entries were found.")
    except OSError as error:
        DIALOG.ok("Noiro Logs", str(error))


def check_update():
    data = xbmcvfs.translatePath("special://profile/addon_data/repository.noiro")
    available_path = os.path.join(data, "available-update.json")
    try:
        with open(available_path, "r", encoding="utf-8") as handle:
            available = json.load(handle)
    except (OSError, ValueError):
        xbmc.executebuiltin("UpdateAddonRepos")
        DIALOG.ok("Noiro Update", "The Noiro repository is still checking for signed releases. Try again shortly.")
        return
    if available.get("up_to_date") or available.get("current") == available.get("available"):
        DIALOG.ok("Noiro Update", "Noiro %s is up to date." % available.get("current"))
        return
    if xbmc.Player().isPlaying():
        DIALOG.ok("Noiro Update", "Stop playback before installing an update.")
        return
    if not DIALOG.yesno("Noiro Update", "Install Noiro %s? The Vero will restart after staging and verify the new build for 45 seconds." % available.get("available")):
        return
    os.makedirs(data, exist_ok=True)
    with open(os.path.join(data, "install-request.json"), "w", encoding="utf-8") as handle:
        json.dump({"version": available.get("available"), "requested_at": int(time.time())}, handle)
    os.chmod(os.path.join(data, "install-request.json"), 0o600)
    notify("Noiro Update", "Update queued; keep playback stopped")


def restore_previous():
    state = call("system.status")
    pending = state.get("boot_pending") or state.get("previous_release") or {}
    if not pending.get("previous_version") or not pending.get("backup"):
        DIALOG.ok("Restore Previous Noiro", "No rollback version is available.")
        return
    if not DIALOG.yesno("Restore Previous Noiro", "Restore Noiro %s after reboot?" % pending["previous_version"]):
        return
    data = xbmcvfs.translatePath("special://profile/addon_data/repository.noiro")
    path = os.path.join(data, "rollback-request.json")
    os.makedirs(data, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(pending, handle)
    os.chmod(path, 0o600)
    call("system.set_maintenance", {"enabled": True})
    set_skin("skin.estuary")
    notify("Noiro", "Rollback was queued; reboot the Vero")


def profile_settings():
    profiles = call("profiles.list")
    profile = next((item for item in profiles if item.get("active")), None)
    if not profile:
        DIALOG.ok("Noiro profile", "Select a profile first.")
        return
    options = [
        "Target subtitle language · %s" % (profile.get("target_language") or "hr"),
        "Gemini auto-translate · %s" % ("On" if profile.get("auto_translate") else "Off"),
        "Link a different Stremio account",
    ]
    selected = DIALOG.select("%s settings" % profile["name"], options)
    if selected == 0:
        language = DIALOG.input(
            "Subtitle language code (for example: hr, en, de)",
            defaultt=profile.get("target_language") or "hr",
            type=xbmcgui.INPUT_ALPHANUM,
        ).strip().lower()
        if language:
            call("profiles.update", {"profile_id": profile["id"], "target_language": language})
    elif selected == 1:
        call("profiles.update", {
            "profile_id": profile["id"],
            "auto_translate": not bool(profile.get("auto_translate")),
        })
        notify("Noiro subtitles", "Gemini auto-translate setting updated")
    elif selected == 2:
        if DIALOG.yesno(
                "Change Stremio account",
                "This unlinks only this Noiro profile. Its previous add-on roster remains available for rollback. Continue?"):
            call("stremio.link.reset", {"profile_id": profile["id"]})
            link_profile(profile["id"])


def settings_menu():
    selected = DIALOG.select("Noiro Settings", ["Noiro Pro", "Profile settings", "Switch profile", "Maintenance"])
    if selected == 0:
        pro_menu()
    elif selected == 1:
        profile_settings()
    elif selected == 2:
        select_profile()
    elif selected == 3:
        maintenance_menu()


def maintenance_menu():
    if not maintenance_pin():
        return
    options = [
        "Open OSMC/Kodi",
        "Update OSMC",
        "Manage Kodi Add-ons",
        "System Information",
        "Noiro Logs",
        "Reset Stremio Link",
        "Check Noiro Update",
        "Restore Previous Noiro",
        "Restore Estuary",
    ]
    selected = DIALOG.select("Noiro Maintenance", options)
    if selected == 0:
        call("system.set_maintenance", {"enabled": True})
        set_skin("skin.estuary")
    elif selected == 1:
        call("system.set_maintenance", {"enabled": True})
        set_skin("skin.estuary")
        DIALOG.ok("Update OSMC", "Open My OSMC from Program add-ons and choose Updates.")
        activate("programs")
    elif selected == 2:
        call("system.set_maintenance", {"enabled": True})
        set_skin("skin.estuary")
        activate("addonbrowser")
    elif selected == 3:
        call("system.set_maintenance", {"enabled": True})
        set_skin("skin.estuary")
        activate("systeminfo")
    elif selected == 4:
        show_logs()
    elif selected == 5:
        profiles = call("profiles.list")
        index = DIALOG.select("Reset Stremio Link", [item["name"] for item in profiles])
        if index >= 0 and DIALOG.yesno("Reset Stremio Link", "Unlink %s?" % profiles[index]["name"]):
            call("stremio.link.reset", {"profile_id": profiles[index]["id"]})
    elif selected == 6:
        check_update()
    elif selected == 7:
        restore_previous()
    elif selected == 8:
        call("system.set_maintenance", {"enabled": True})
        set_skin("skin.estuary")


def first_setup():
    health = call("system.health")
    if not health.get("ready") or not (health.get("native") or {}).get("ready"):
        DIALOG.ok("Noiro setup", "The native Noiro engine did not pass its health check. Estuary will remain active.")
        return
    profiles = call("profiles.list")
    if not profiles:
        profile = create_profile()
        if not profile:
            return
    call("system.set_enabled", {"enabled": True})
    call("system.set_maintenance", {"enabled": False})
    set_skin("skin.noiro")
    select_profile(boot=True)


def main():
    action = params().get("action") or "menu"
    try:
        if action == "profiles":
            select_profile(boot=params().get("boot") == "1")
        elif action == "first_setup":
            first_setup()
        elif action == "open_osmc":
            open_osmc()
        elif action == "return_noiro":
            return_noiro()
        elif action == "maintenance":
            maintenance_menu()
        elif action == "settings":
            settings_menu()
        elif action == "pro":
            pro_menu()
        else:
            status = call("system.status")
            if status.get("maintenance_mode"):
                return_noiro()
            else:
                select_profile()
    except RpcError as error:
        DIALOG.ok("Noiro", "The Noiro service is not ready:\n%s" % error)


if __name__ == "__main__":
    main()
