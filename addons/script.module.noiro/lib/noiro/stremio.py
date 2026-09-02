import datetime
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .models import LinkSession, StreamCandidate


class StremioError(RuntimeError):
    pass


class LinkPending(Exception):
    pass


class StremioClient(object):
    LINK_API = "https://link.stremio.com/api/v2"
    ACCOUNT_API = "https://api.strem.io/api"
    PENDING_CODE = 101

    def __init__(self, timeout=20, opener=None):
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    def _request(self, url, payload=None, headers=None):
        body = None
        request_headers = {"Accept": "application/json", "User-Agent": "Noiro-Kodi/0.1"}
        request_headers.update(headers or {})
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=request_headers, method="POST" if body else "GET")
        try:
            with self.opener(request, timeout=self.timeout) as response:
                data = response.read()
        except (urllib.error.URLError, OSError) as error:
            raise StremioError("Stremio request failed: %s" % error)
        try:
            return json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise StremioError("Stremio returned invalid JSON: %s" % error)

    @staticmethod
    def _result(payload):
        error = payload.get("error") if isinstance(payload, dict) else None
        if error:
            raise StremioError(error.get("message") or "Stremio request failed")
        return payload.get("result") if isinstance(payload, dict) else None

    def create_link(self):
        payload = self._request(self.LINK_API + "/create?type=Create")
        result = self._result(payload) or {}
        if not result.get("code"):
            raise StremioError("Could not create a Stremio link code")
        return LinkSession(
            code=result["code"],
            link=result.get("link", ""),
            qrcode=result.get("qrcode", ""),
            expires_at=time.time() + 300,
        )

    def read_link(self, code):
        encoded = urllib.parse.quote(str(code), safe="")
        payload = self._request(self.LINK_API + "/read?type=Read&code=" + encoded)
        result = payload.get("result") or {}
        auth_key = result.get("authKey") or result.get("auth_key")
        if auth_key:
            return auth_key
        error = payload.get("error") or {}
        if error.get("code") == self.PENDING_CODE or not error:
            raise LinkPending()
        raise StremioError(error.get("message") or "Stremio link failed")

    def validate(self, auth_key):
        payload = self._request(self.ACCOUNT_API + "/getUser", {"authKey": auth_key})
        result = self._result(payload)
        if not isinstance(result, dict):
            raise StremioError("This Stremio session is no longer valid")
        return result

    def addons(self, auth_key):
        result = self._result(self._request(
            self.ACCOUNT_API + "/addonCollectionGet",
            {"authKey": auth_key, "update": True},
        )) or {}
        return list(result.get("addons") or [])

    def set_addons(self, auth_key, addons):
        payload = self._request(
            self.ACCOUNT_API + "/addonCollectionSet",
            {"authKey": auth_key, "addons": addons},
        )
        self._result(payload)
        return True

    def library(self, auth_key):
        result = self._result(self._request(
            self.ACCOUNT_API + "/datastoreGet",
            {"authKey": auth_key, "collection": "libraryItem", "all": True},
        ))
        return list(result or [])

    def library_item(self, auth_key, item_id):
        result = self._result(self._request(
            self.ACCOUNT_API + "/datastoreGet",
            {"authKey": auth_key, "collection": "libraryItem", "ids": [item_id], "all": False},
        ))
        return (result or [None])[0]

    def save_progress(self, auth_key, meta, position_seconds, duration_seconds):
        if duration_seconds <= 0 or position_seconds < 0:
            return False
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        library_id = meta["libraryId"]
        item = self.library_item(auth_key, library_id) or {
            "_id": library_id,
            "name": meta.get("name") or library_id,
            "type": meta.get("type") or "movie",
            "posterShape": "poster",
            "poster": meta.get("poster"),
            "removed": False,
            "temp": False,
            "_ctime": now,
            "behaviorHints": {"defaultVideoId": None},
            "state": {},
        }
        state = dict(item.get("state") or {})
        state.update({
            "timeOffset": int(round(position_seconds * 1000)),
            "duration": int(round(duration_seconds * 1000)),
            "lastWatched": now,
            "video_id": meta.get("videoId") or library_id,
        })
        item.update({"state": state, "_mtime": now, "removed": False})
        self._result(self._request(
            self.ACCOUNT_API + "/datastorePut",
            {"authKey": auth_key, "collection": "libraryItem", "changes": [item]},
        ))
        return True

    def manifest(self, manifest_url):
        if not str(manifest_url).startswith(("https://", "http://")):
            raise StremioError("Manifest URL must use HTTP or HTTPS")
        payload = self._request(manifest_url)
        manifest = payload.get("manifest") if isinstance(payload, dict) and "manifest" in payload else payload
        if not isinstance(manifest, dict) or not manifest.get("id") or not manifest.get("resources"):
            raise StremioError("The URL did not return a valid Stremio manifest")
        return {"transportUrl": manifest_url, "manifest": manifest}

    @staticmethod
    def addon_base(descriptor):
        return str(descriptor.get("transportUrl") or "").rsplit("/manifest.json", 1)[0]

    def resource(self, descriptor, resource, content_type, item_id, extras=None):
        base = self.addon_base(descriptor)
        path = "/%s/%s/%s" % (
            urllib.parse.quote(resource, safe=""),
            urllib.parse.quote(content_type, safe=""),
            urllib.parse.quote(item_id, safe=":._-"),
        )
        if extras:
            encoded = urllib.parse.urlencode(extras)
            path += "/" + urllib.parse.quote(encoded, safe="=&")
        return self._request(base + path + ".json")

    @staticmethod
    def _resource_names(manifest):
        return [item if isinstance(item, str) else item.get("name") for item in manifest.get("resources") or []]

    def catalogs(self, addons, extras=None):
        rows = []
        for descriptor in addons:
            manifest = descriptor.get("manifest") or {}
            if "catalog" not in self._resource_names(manifest):
                continue
            for catalog in manifest.get("catalogs") or []:
                if not isinstance(catalog, dict) or not catalog.get("id") or not catalog.get("type"):
                    continue
                try:
                    payload = self.resource(
                        descriptor,
                        "catalog",
                        catalog["type"],
                        catalog["id"],
                        extras=extras,
                    )
                except StremioError:
                    continue
                rows.append({
                    "addonId": manifest.get("id"),
                    "addonName": manifest.get("name"),
                    "catalogId": catalog["id"],
                    "catalogName": catalog.get("name") or catalog["id"],
                    "type": catalog["type"],
                    "metas": list(payload.get("metas") or []),
                })
        return rows

    def search(self, addons, query):
        clean = str(query or "").strip()
        if not clean:
            return []
        return self.catalogs(addons, {"search": clean})

    def metadata(self, addons, content_type, item_id):
        for descriptor in addons:
            manifest = descriptor.get("manifest") or {}
            if "meta" not in self._resource_names(manifest):
                continue
            types = manifest.get("types") or []
            if types and content_type not in types:
                continue
            try:
                payload = self.resource(descriptor, "meta", content_type, item_id)
            except StremioError:
                continue
            if isinstance(payload.get("meta"), dict):
                value = dict(payload["meta"])
                value["addonName"] = manifest.get("name")
                return value
        return None

    def streams(self, addons, content_type, video_id):
        results = []
        for descriptor in addons:
            manifest = descriptor.get("manifest") or {}
            if "stream" not in self._resource_names(manifest):
                continue
            try:
                payload = self.resource(descriptor, "stream", content_type, video_id)
            except StremioError:
                continue
            for index, item in enumerate(payload.get("streams") or []):
                hints = item.get("behaviorHints") or {}
                candidate = StreamCandidate(
                    id="%s:%d" % (manifest.get("id", "addon"), index),
                    title=item.get("title") or item.get("name") or manifest.get("name") or "Stream",
                    url=item.get("url"),
                    info_hash=item.get("infoHash"),
                    file_idx=item.get("fileIdx"),
                    addon_name=manifest.get("name"),
                    headers=dict(hints.get("proxyHeaders", {}).get("request", {}) or {}),
                    behavior_hints=hints,
                )
                results.append(candidate.to_dict())
        return results

    def subtitles(self, addons, content_type, video_id):
        results = []
        for descriptor in addons:
            manifest = descriptor.get("manifest") or {}
            if "subtitles" not in self._resource_names(manifest):
                continue
            types = manifest.get("types") or []
            if types and content_type not in types:
                continue
            try:
                payload = self.resource(descriptor, "subtitles", content_type, video_id)
            except StremioError:
                continue
            for index, item in enumerate(payload.get("subtitles") or []):
                subtitle_url = item.get("url")
                if not subtitle_url:
                    continue
                results.append({
                    "id": item.get("id") or "%s:%d" % (manifest.get("id", "addon"), index),
                    "lang": item.get("lang") or item.get("language") or "und",
                    "url": subtitle_url,
                    "addonName": manifest.get("name"),
                })
        return results
