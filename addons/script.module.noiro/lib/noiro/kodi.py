import json
import os
import re
import tempfile
import time


_SKIN_SETTING = re.compile(
    r'(<setting\s+id=["\']lookandfeel\.skin["\'][^>]*>)(.*?)(</setting>)',
    re.DOTALL,
)


def available():
    try:
        import xbmc  # noqa: F401
        return True
    except ImportError:
        return False


def json_rpc(method, params=None):
    import xbmc  # type: ignore
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    response = json.loads(xbmc.executeJSONRPC(json.dumps(payload)))
    if response.get("error"):
        raise RuntimeError(response["error"].get("message") or "Kodi JSON-RPC failed")
    return response.get("result")


def _replace_skin_setting(document, skin_id):
    if not re.match(r"^[A-Za-z0-9._-]+$", skin_id):
        raise ValueError("Invalid Kodi skin identifier")
    updated, count = _SKIN_SETTING.subn(
        lambda match: "%s%s%s" % (match.group(1), skin_id, match.group(3)),
        document,
        count=1,
    )
    if count != 1:
        raise RuntimeError("Kodi skin setting was not found in guisettings.xml")
    return updated


def _persist_skin_setting(skin_id):
    """Persist the selected skin without rewriting unrelated Kodi settings.

    Kodi's JSON-RPC setter changes the live setting but does not save
    guisettings.xml itself. OSMC can restart Kodi before the normal settings
    shutdown path runs, so an otherwise successful skin selection would fall
    back to the previous skin after a reboot. Replace only this one XML value and use an
    atomic rename so a power loss cannot leave a partial settings file.
    """
    import xbmcvfs  # type: ignore

    path = xbmcvfs.translatePath("special://profile/guisettings.xml")
    with open(path, "r", encoding="utf-8") as handle:
        original = handle.read()
    updated = _replace_skin_setting(original, skin_id)
    if updated == original:
        return

    directory = os.path.dirname(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".noiro-guisettings-", dir=directory)
    try:
        os.fchmod(descriptor, os.stat(path).st_mode & 0o777)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)


def _confirm_skin_change(timeout=5.0):
    """Accept Kodi's own ten-second skin confirmation dialog.

    The dialog defaults to No. Waiting for and confirming it before opening a
    Noiro profile dialog prevents the two modal windows from covering one
    another and silently reverting the skin.
    """
    import xbmc  # type: ignore

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        properties = json_rpc(
            "GUI.GetProperties",
            {"properties": ["currentwindow", "currentcontrol"]},
        ) or {}
        window = properties.get("currentwindow") or {}
        if int(window.get("id") or 0) == 10100:
            # Kodi's confirmation always starts on No. Move to Yes and select.
            json_rpc("Input.Left")
            xbmc.sleep(100)
            focused = json_rpc(
                "GUI.GetProperties",
                {"properties": ["currentwindow", "currentcontrol"]},
            ) or {}
            if int((focused.get("currentwindow") or {}).get("id") or 0) != 10100:
                raise RuntimeError("Kodi closed the skin confirmation before it could be accepted")
            json_rpc("Input.Select")
            return True
        xbmc.sleep(100)
    return False


def set_skin(skin_id):
    current = json_rpc("Settings.GetSettingValue", {"setting": "lookandfeel.skin"}) or {}
    if current.get("value") == skin_id:
        _persist_skin_setting(skin_id)
        return True
    # Kodi reloads the GUI as part of changing lookandfeel.skin. Calling
    # ReloadSkin immediately afterwards caused two complete skin loads on the
    # Vero and made an already fragile transition less reliable.
    changed = json_rpc("Settings.SetSettingValue", {"setting": "lookandfeel.skin", "value": skin_id})
    if changed is not True:
        raise RuntimeError("Kodi refused to change the active skin")
    _confirm_skin_change()
    selected = json_rpc("Settings.GetSettingValue", {"setting": "lookandfeel.skin"}) or {}
    if selected.get("value") != skin_id:
        raise RuntimeError("Kodi reverted the requested skin")
    _persist_skin_setting(skin_id)
    return True


def activate(window, path=None):
    import xbmc  # type: ignore
    if path:
        xbmc.executebuiltin("ActivateWindow(%s,%s)" % (window, path))
    else:
        xbmc.executebuiltin("ActivateWindow(%s)" % window)
