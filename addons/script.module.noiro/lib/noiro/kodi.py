import json


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


def set_skin(skin_id):
    result = json_rpc("Settings.SetSettingValue", {"setting": "lookandfeel.skin", "value": skin_id})
    import xbmc  # type: ignore
    xbmc.executebuiltin("ReloadSkin()")
    return result


def activate(window, path=None):
    import xbmc  # type: ignore
    if path:
        xbmc.executebuiltin("ActivateWindow(%s,%s)" % (window, path))
    else:
        xbmc.executebuiltin("ActivateWindow(%s)" % window)
