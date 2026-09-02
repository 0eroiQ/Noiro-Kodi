import json
import sys
import urllib.parse

import xbmc  # type: ignore
import xbmcgui  # type: ignore
import xbmcplugin  # type: ignore

from noiro.paths import socket_path
from noiro.rpc import JsonRpcClient, RpcError


BASE = sys.argv[0]
HANDLE = int(sys.argv[1])
RPC = JsonRpcClient(socket_path())
DIALOG = xbmcgui.Dialog()
TRANSIENT_RPC_MARKERS = (
    "no such file or directory",
    "connection refused",
    "connection reset",
    "timed out",
    "temporarily unavailable",
)


def request(method, params=None):
    """Wait briefly for the Noiro service during Kodi's cold-start race.

    Kodi can evaluate Home widget paths before Python add-on services have
    finished starting.  A bounded retry keeps those first widget requests
    alive without hiding real Stremio or application errors.
    """
    for attempt in range(61):
        try:
            return RPC.call(method, params or {})
        except RpcError as error:
            transient = any(marker in str(error).lower() for marker in TRANSIENT_RPC_MARKERS)
            if not transient or attempt == 60:
                raise
            xbmc.sleep(250)


def url(action, **params):
    value = {"action": action}
    value.update({key: str(item) for key, item in params.items() if item is not None})
    return BASE + "?" + urllib.parse.urlencode(value)


def query():
    value = urllib.parse.parse_qs(sys.argv[2].lstrip("?")) if len(sys.argv) > 2 else {}
    return {key: items[-1] for key, items in value.items()}


def list_item(label, path, folder=True, art=None, info=None, playable=False, context=None, properties=None):
    item = xbmcgui.ListItem(label=label)
    if art:
        item.setArt(art)
    if info:
        item.setInfo("video", info)
    if playable:
        item.setProperty("IsPlayable", "true")
    if context:
        item.addContextMenuItems(context)
    for key, value in (properties or {}).items():
        if value is not None:
            item.setProperty(str(key), str(value))
    xbmcplugin.addDirectoryItem(HANDLE, path, item, isFolder=folder)


def meta_art(meta):
    background = meta.get("background") or meta.get("poster") or ""
    return {
        "thumb": meta.get("poster") or background,
        "poster": meta.get("poster") or "",
        "fanart": background,
        "landscape": background,
        "banner": background,
        "clearlogo": meta.get("logo") or "",
    }


def meta_info(meta):
    return {
        "title": meta.get("name") or meta.get("title") or meta.get("id") or "Untitled",
        "plot": meta.get("description") or "",
        "year": meta.get("releaseInfo") or meta.get("year") or "",
        "genre": meta.get("genres") or [],
        "rating": float(meta.get("imdbRating") or 0),
        "mediatype": "tvshow" if meta.get("type") == "series" else "movie",
    }


def add_meta(meta, resume_position_ms=None, resume_duration_ms=None, properties=None):
    content_type = meta.get("type") or "movie"
    item_id = meta.get("id")
    if not item_id:
        return
    values = dict(properties or {})
    values["Noiro.Type"] = content_type
    position = float(resume_position_ms or 0)
    duration = float(resume_duration_ms or 0)
    if position > 0 and duration > 0:
        values["ResumeTime"] = position / 1000.0
        values["TotalTime"] = duration / 1000.0
        values["Noiro.Progress"] = max(0.0, min(100.0, position * 100.0 / duration))
    list_item(
        meta.get("name") or item_id,
        url(
            "details",
            type=content_type,
            id=item_id,
            resume_position_ms=resume_position_ms,
            resume_duration_ms=resume_duration_ms,
        ),
        folder=True,
        art=meta_art(meta),
        info=meta_info(meta),
        properties=values,
    )


def collection_name(row):
    catalog = row.get("catalogName") or "Catalog"
    addon = row.get("addonName") or "Stremio"
    return "%s · %s" % (catalog, addon)


def widget_featured():
    """Blend the active profile's catalog rails into one hero/featured feed.

    Round-robin order prevents the first installed add-on from occupying the
    entire shelf while keeping the user's real Stremio add-on order intact.
    """
    rows = [row for row in request("stremio.catalogs") if row.get("metas")]
    seen = set()
    added = 0
    depth = max([len(row.get("metas") or []) for row in rows] or [0])
    for index in range(depth):
        for row in rows:
            metas = row.get("metas") or []
            if index >= len(metas):
                continue
            meta = metas[index]
            identity = (meta.get("type"), meta.get("id"))
            if not identity[1] or identity in seen:
                continue
            seen.add(identity)
            add_meta(meta, properties={"Noiro.Collection": collection_name(row)})
            added += 1
            if added >= 36:
                break
        if added >= 36:
            break
    xbmcplugin.setPluginCategory(HANDLE, "Featured")
    xbmcplugin.setContent(HANDLE, "movies")
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def widget_catalog(values):
    rows = [row for row in request("stremio.catalogs") if row.get("metas")]
    slot = max(0, int(values.get("slot") or 0))
    if slot < len(rows):
        row = rows[slot]
        label = collection_name(row)
        for meta in row.get("metas") or []:
            add_meta(meta, properties={"Noiro.Collection": label})
        xbmcplugin.setPluginCategory(HANDLE, label)
    xbmcplugin.setContent(HANDLE, "movies")
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def active_profile():
    profiles = request("profiles.list")
    return next((item for item in profiles if item.get("active")), None)


def home():
    profile = active_profile()
    if not profile:
        xbmc.executebuiltin("RunAddon(script.noiro.setup,?action=profiles)")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    xbmcplugin.setPluginCategory(HANDLE, "NoiroTV · %s" % profile["name"])
    list_item("Continue Watching", url("continue"), folder=True)
    list_item("Search", url("search"), folder=True)
    list_item("Library", url("library"), folder=True)
    list_item("Add-ons", url("addons"), folder=True)
    try:
        pro = request("pro.status")
        list_item("Noiro Pro · %s" % ("PRO" if pro.get("pro") else "Free"), url("open_pro"), folder=False)
    except RpcError:
        list_item("Noiro Pro · Free", url("open_pro"), folder=False)
    list_item("Settings", url("open_settings"), folder=False)
    try:
        for row in request("stremio.catalogs"):
            label = "%s · %s" % (row.get("addonName") or "Stremio", row.get("catalogName") or "Catalog")
            list_item(label, url("catalog", addon=row.get("addonId"), catalog=row.get("catalogId"), type=row.get("type")), folder=True)
    except RpcError as error:
        DIALOG.notification("NoiroTV", str(error), xbmcgui.NOTIFICATION_ERROR, 5000)
    xbmcplugin.setContent(HANDLE, "videos")
    xbmcplugin.endOfDirectory(HANDLE)


def catalog(values):
    for row in request("stremio.catalogs"):
        if row.get("addonId") == values.get("addon") and row.get("catalogId") == values.get("catalog") and row.get("type") == values.get("type"):
            xbmcplugin.setPluginCategory(HANDLE, row.get("catalogName") or "Noiro")
            for meta in row.get("metas") or []:
                add_meta(meta)
            break
    xbmcplugin.setContent(HANDLE, "movies")
    xbmcplugin.endOfDirectory(HANDLE)


def search():
    term = DIALOG.input("Search Stremio", type=xbmcgui.INPUT_ALPHANUM)
    if term:
        seen = set()
        for row in request("stremio.search", {"query": term}):
            for meta in row.get("metas") or []:
                identity = (meta.get("type"), meta.get("id"))
                if identity not in seen:
                    seen.add(identity)
                    add_meta(meta)
    xbmcplugin.setContent(HANDLE, "videos")
    xbmcplugin.endOfDirectory(HANDLE)


def library():
    for item in request("stremio.library"):
        if item.get("removed"):
            continue
        meta = dict(item)
        meta.setdefault("id", item.get("_id"))
        meta.setdefault("name", item.get("name"))
        add_meta(meta)
    xbmcplugin.setContent(HANDLE, "videos")
    xbmcplugin.endOfDirectory(HANDLE)


def widget_library():
    for item in request("stremio.library"):
        if item.get("removed"):
            continue
        meta = dict(item)
        meta.setdefault("id", item.get("_id"))
        meta.setdefault("name", item.get("name"))
        add_meta(meta, properties={"Noiro.Collection": "My Library"})
    xbmcplugin.setPluginCategory(HANDLE, "My Library")
    xbmcplugin.setContent(HANDLE, "videos")
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def continue_watching():
    for item in request("stremio.continue"):
        meta = dict(item)
        meta.setdefault("id", item.get("_id"))
        meta.setdefault("name", item.get("name"))
        state = item.get("state") or {}
        video_id = state.get("video_id") or meta.get("id")
        if meta.get("type") == "series" and video_id != meta.get("id"):
            list_item(
                meta.get("name") or video_id,
                url(
                    "sources", type="series", id=meta.get("id"), video_id=video_id,
                    title=meta.get("name"), resume_position_ms=item.get("resume_position_ms"),
                    resume_duration_ms=item.get("resume_duration_ms"),
                ),
                folder=True,
                art=meta_art(meta),
                info=meta_info(meta),
                properties={
                    "Noiro.Collection": "Continue Watching",
                    "ResumeTime": float(item.get("resume_position_ms") or 0) / 1000.0,
                    "TotalTime": float(item.get("resume_duration_ms") or 0) / 1000.0,
                    "Noiro.Progress": (
                        float(item.get("resume_position_ms") or 0) * 100.0
                        / max(1.0, float(item.get("resume_duration_ms") or 0))
                    ),
                },
            )
        else:
            add_meta(
                meta,
                item.get("resume_position_ms"),
                item.get("resume_duration_ms"),
                properties={"Noiro.Collection": "Continue Watching"},
            )
    xbmcplugin.setPluginCategory(HANDLE, "Continue Watching")
    xbmcplugin.setContent(HANDLE, "videos")
    xbmcplugin.endOfDirectory(HANDLE)


def widget_continue_watching():
    continue_watching()


def details(values):
    meta = request("stremio.metadata", {"type": values.get("type"), "id": values.get("id")}) or {
        "id": values.get("id"), "type": values.get("type"), "name": values.get("id")
    }
    videos = meta.get("videos") or []
    if videos:
        for video in videos:
            video_id = video.get("id")
            label = video.get("title") or video.get("name") or video_id
            list_item(label, url(
                "sources", type=meta.get("type"), id=meta.get("id"), video_id=video_id,
                title=meta.get("name"), resume_position_ms=values.get("resume_position_ms"),
                resume_duration_ms=values.get("resume_duration_ms")), folder=True, art=meta_art(meta))
    else:
        video_id = meta.get("id")
        list_item("Play", url(
            "play", type=meta.get("type"), id=meta.get("id"), video_id=video_id,
            title=meta.get("name"), resume_position_ms=values.get("resume_position_ms"),
            resume_duration_ms=values.get("resume_duration_ms")), folder=False, playable=True, art=meta_art(meta))
        list_item("Sources", url(
            "sources", type=meta.get("type"), id=meta.get("id"), video_id=video_id,
            title=meta.get("name"), resume_position_ms=values.get("resume_position_ms"),
            resume_duration_ms=values.get("resume_duration_ms")), folder=True)
    xbmcplugin.setContent(HANDLE, "episodes" if videos else "movies")
    xbmcplugin.endOfDirectory(HANDLE)


def stream_score(item):
    text = (item.get("title") or "").lower()
    return (
        100 if item.get("url") else -1000,
        20 if "4k" in text or "2160" in text else 0,
        10 if "debrid" in text else 0,
        5 if "hevc" in text or "h265" in text else 0,
    )


def playback_url(item):
    value = item.get("url") or ""
    headers = item.get("headers") or {}
    if headers:
        value += "|" + urllib.parse.urlencode(headers)
    return value


def start_playback(values, stream):
    profile = active_profile() or {}
    meta = {
        "profile_id": profile.get("id"),
        "libraryId": values.get("id"),
        "videoId": values.get("video_id"),
        "name": values.get("title") or values.get("id"),
        "type": values.get("type") or "movie",
        "resume_seconds": float(values.get("resume_position_ms") or 0) / 1000.0,
    }
    window = xbmcgui.Window(10000)
    window.setProperty("Noiro.PlaybackMeta", json.dumps(meta))
    window.clearProperty("Noiro.SubtitleJob")
    item = xbmcgui.ListItem(path=playback_url(stream))
    item.setProperty("IsPlayable", "true")
    try:
        subtitle = request("subtitle.prepare", {
            "profile_id": profile.get("id"),
            "type": values.get("type") or "movie",
            "video_id": values.get("video_id"),
        }) or {}
        if subtitle.get("path"):
            item.setSubtitles([subtitle["path"]])
        if subtitle.get("job_id"):
            window.setProperty("Noiro.SubtitleJob", json.dumps({"job_id": subtitle["job_id"]}))
    except Exception as error:
        # Subtitle discovery and Gemini are deliberately fail-soft: the video
        # must start even when an add-on, network call, or translation fails.
        xbmc.log("Noiro subtitle fallback: %s" % error, xbmc.LOGWARNING)
    xbmcplugin.setResolvedUrl(HANDLE, True, item)


def play(values):
    streams = request("stremio.streams", {"type": values.get("type"), "video_id": values.get("video_id")})
    playable = sorted((item for item in streams if item.get("playable")), key=stream_score, reverse=True)
    if not playable:
        DIALOG.ok("NoiroTV", "No direct or debrid-resolved stream is available. Raw torrent results remain locked in this release.")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    start_playback(values, playable[0])


def sources(values):
    streams = request("stremio.streams", {"type": values.get("type"), "video_id": values.get("video_id")})
    for index, stream in enumerate(sorted(streams, key=stream_score, reverse=True)):
        if stream.get("playable"):
            forwarded = dict(values)
            forwarded.pop("action", None)
            list_item(stream.get("title") or "Direct stream", url("play_source", source=index, **forwarded), folder=False, playable=True)
        else:
            list_item("🔒 %s  · Torrent engine not included" % (stream.get("title") or "Raw torrent"), "", folder=False)
    xbmcplugin.endOfDirectory(HANDLE)


def play_source(values):
    streams = sorted(request("stremio.streams", {"type": values.get("type"), "video_id": values.get("video_id")}), key=stream_score, reverse=True)
    index = int(values.get("source") or 0)
    if index >= len(streams) or not streams[index].get("playable"):
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    start_playback(values, streams[index])


def addons():
    for addon in request("stremio.addons.list"):
        manifest = addon.get("manifest") or {}
        addon_id = manifest.get("id")
        context = [("Remove add-on", "RunPlugin(%s)" % url("remove_addon", addon_id=addon_id, name=manifest.get("name")))]
        list_item(manifest.get("name") or addon_id, "", folder=False, context=context)
    list_item("＋ Install from manifest URL", url("install_addon"), folder=False)
    list_item("↶ Restore previous add-on roster", url("restore_addons"), folder=False)
    xbmcplugin.endOfDirectory(HANDLE)


def install_addon():
    manifest_url = DIALOG.input("Stremio manifest URL", type=xbmcgui.INPUT_ALPHANUM)
    if manifest_url and DIALOG.yesno("Install Stremio add-on", "This changes the active profile's real Stremio account. Continue?"):
        request("stremio.addons.install", {"manifest_url": manifest_url, "confirmed": True})
        xbmc.executebuiltin("Container.Refresh")


def remove_addon(values):
    if DIALOG.yesno("Remove Stremio add-on", "Remove %s from the active profile's real Stremio account?" % (values.get("name") or values.get("addon_id"))):
        request("stremio.addons.remove", {"addon_id": values.get("addon_id"), "confirmed": True})
        xbmc.executebuiltin("Container.Refresh")


def restore_addons():
    backups = request("stremio.addons.backups")
    if not backups:
        DIALOG.ok("Noiro add-ons", "No previous add-on roster is available for this profile.")
        return
    labels = ["%d add-ons · %s" % (item.get("addon_count") or 0, ", ".join(filter(None, item.get("names") or []))[:120])
              for item in backups]
    selected = DIALOG.select("Restore Stremio add-ons", labels)
    if selected >= 0 and DIALOG.yesno(
            "Restore Stremio add-ons",
            "This changes the active profile's real Stremio account and first backs up its current roster. Continue?"):
        request("stremio.addons.restore", {"backup_id": backups[selected]["id"], "confirmed": True})
        xbmc.executebuiltin("Container.Refresh")


def open_settings():
    xbmc.executebuiltin("RunAddon(script.noiro.setup,?action=settings)")
    xbmcplugin.endOfDirectory(HANDLE)


def open_pro():
    xbmc.executebuiltin("RunAddon(script.noiro.setup,?action=pro)")
    xbmcplugin.endOfDirectory(HANDLE)


def main():
    values = query()
    action = values.get("action") or "home"
    routes = {
        "home": lambda: home(),
        "catalog": lambda: catalog(values),
        "search": lambda: search(),
        "library": lambda: library(),
        "continue": lambda: continue_watching(),
        "widget_featured": lambda: widget_featured(),
        "widget_catalog": lambda: widget_catalog(values),
        "widget_continue": lambda: widget_continue_watching(),
        "widget_library": lambda: widget_library(),
        "details": lambda: details(values),
        "play": lambda: play(values),
        "sources": lambda: sources(values),
        "play_source": lambda: play_source(values),
        "addons": lambda: addons(),
        "install_addon": lambda: install_addon(),
        "remove_addon": lambda: remove_addon(values),
        "restore_addons": lambda: restore_addons(),
        "open_settings": lambda: open_settings(),
        "open_pro": lambda: open_pro(),
    }
    try:
        routes.get(action, routes["home"])()
    except RpcError as error:
        if action.startswith("widget_"):
            xbmc.log("Noiro home widget unavailable: %s" % error, xbmc.LOGWARNING)
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)
            return
        DIALOG.ok("NoiroTV", str(error))
        if action.startswith("play"):
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        else:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


if __name__ == "__main__":
    main()
