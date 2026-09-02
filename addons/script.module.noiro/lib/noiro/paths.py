import os
import tempfile


def translate_path(value):
    try:
        import xbmcvfs  # type: ignore
        return xbmcvfs.translatePath(value)
    except (ImportError, AttributeError):
        if str(value).startswith("special://"):
            relative = str(value).split("special://", 1)[1].replace(":", "_").lstrip("/")
            return os.path.join(tempfile.gettempdir(), "noiro-kodi-dev", relative)
        return os.path.abspath(os.path.expanduser(value))


def addon_data(addon_id):
    override = os.environ.get("NOIRO_DATA_DIR")
    if override:
        root = os.path.join(override, addon_id)
    else:
        root = translate_path("special://profile/addon_data/%s" % addon_id)
    os.makedirs(root, mode=0o700, exist_ok=True)
    return root


def service_data():
    return addon_data("script.service.noiro")


def repository_data():
    return addon_data("repository.noiro")


def socket_path():
    override = os.environ.get("NOIRO_SOCKET_PATH")
    if override:
        return override
    return os.path.join(service_data(), "noiro-v1.sock")


def engine_socket_path():
    override = os.environ.get("NOIRO_ENGINE_SOCKET_PATH")
    if override:
        return override
    return os.path.join(service_data(), "noiro-engine-v1.sock")
