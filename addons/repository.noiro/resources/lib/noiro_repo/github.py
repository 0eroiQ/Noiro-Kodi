import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .security import sha256_bytes, verify_rsa_sha256


class ReleaseError(RuntimeError):
    pass


class GitHubReleaseClient(object):
    API = "https://api.github.com/repos/0eroiQ/Noiro-Kodi"

    def __init__(self, token, cache_dir, public_key_path, timeout=30, opener=None):
        self.token = token
        self.cache_dir = cache_dir
        self.public_key_path = public_key_path
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen
        self.release = None
        self.manifest = None

    def _request(self, url, accept="application/vnd.github+json"):
        headers = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Noiro-Kodi/0.2.0",
        }
        if self.token:
            headers["Authorization"] = "Bearer %s" % self.token
        request = urllib.request.Request(
            url,
            headers=headers,
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                return response.read()
        except (urllib.error.URLError, OSError) as error:
            raise ReleaseError("Release request failed: %s" % error)

    def validate_repository(self):
        raw = self._request(self.API)
        payload = json.loads(raw.decode("utf-8"))
        return payload.get("full_name") == "0eroiQ/Noiro-Kodi" and not bool(payload.get("private"))

    def load_latest(self, force=False):
        if self.release and self.manifest and not force:
            return self.manifest
        release = json.loads(self._request(self.API + "/releases/latest").decode("utf-8"))
        assets = {item["name"]: item for item in release.get("assets") or []}
        if "release-manifest.json" not in assets or "release-manifest.sig" not in assets:
            raise ReleaseError("Release does not contain a signed manifest")
        manifest_bytes = self._download_asset(assets["release-manifest.json"])
        signature_text = self._download_asset(assets["release-manifest.sig"]).strip()
        try:
            signature = base64.b64decode(signature_text, validate=True)
            with open(self.public_key_path, "r", encoding="utf-8") as handle:
                public_key = json.load(handle)
        except (OSError, ValueError) as error:
            raise ReleaseError("Release signature metadata is invalid: %s" % error)
        if not verify_rsa_sha256(manifest_bytes, signature, public_key):
            raise ReleaseError("Release signature verification failed")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if manifest.get("schema") != 1 or int(manifest.get("kodi_major") or 0) != 21:
            raise ReleaseError("Release is not compatible with Kodi 21")
        self.release = release
        self.manifest = manifest
        self.assets = assets
        return manifest

    def _download_asset(self, asset):
        return self._request(asset["url"], accept="application/octet-stream")

    def asset(self, name):
        manifest = self.load_latest()
        expected = {item["name"]: item for item in manifest.get("artifacts") or []}.get(name)
        asset = self.assets.get(name)
        if not expected or not asset:
            raise ReleaseError("Unknown release artifact")
        os.makedirs(self.cache_dir, mode=0o700, exist_ok=True)
        path = os.path.join(self.cache_dir, os.path.basename(name))
        try:
            with open(path, "rb") as handle:
                cached = handle.read()
            if sha256_bytes(cached) == expected["sha256"] and len(cached) == int(expected["size"]):
                return cached
        except OSError:
            pass
        value = self._download_asset(asset)
        if sha256_bytes(value) != expected["sha256"] or len(value) != int(expected["size"]):
            raise ReleaseError("Artifact checksum verification failed")
        temporary = path + ".tmp"
        with open(temporary, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        return value

    def repository_asset(self, request_path):
        name = os.path.basename(urllib.parse.unquote(request_path))
        if name == "addons.xml.sha256":
            name = "addons.xml.sha256"
        return name, self.asset(name)
