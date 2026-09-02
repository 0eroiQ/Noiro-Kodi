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
    RELEASES = "https://github.com/0eroiQ/Noiro-Kodi/releases"
    LATEST = RELEASES + "/latest/download"

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
            "User-Agent": "Noiro-Kodi/0.3.0",
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
        manifest = self.load_latest()
        return manifest.get("product") == "Noiro-Kodi"

    def load_latest(self, force=False):
        if self.release and self.manifest and not force:
            return self.manifest
        cache_buster = "?noiro=" + str(int(time.time()))
        manifest_bytes = self._request(
            self.LATEST + "/release-manifest.json" + cache_buster,
            accept="application/octet-stream",
        )
        signature_text = self._request(
            self.LATEST + "/release-manifest.sig" + cache_buster,
            accept="application/octet-stream",
        ).strip()
        try:
            signature = base64.b64decode(signature_text, validate=True)
            with open(self.public_key_path, "r", encoding="utf-8") as handle:
                public_key = json.load(handle)
        except (OSError, ValueError) as error:
            raise ReleaseError("Release signature metadata is invalid: %s" % error)
        if not verify_rsa_sha256(manifest_bytes, signature, public_key):
            raise ReleaseError("Release signature verification failed")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if (manifest.get("schema") != 1 or manifest.get("product") != "Noiro-Kodi"
                or int(manifest.get("kodi_major") or 0) != 21):
            raise ReleaseError("Release is not compatible with Kodi 21")
        self.release = {"tag_name": "v" + str(manifest.get("version"))}
        self.manifest = manifest
        return manifest

    def asset(self, name):
        manifest = self.load_latest()
        expected = {item["name"]: item for item in manifest.get("artifacts") or []}.get(name)
        if not expected:
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
        version = str(manifest.get("version") or "")
        if not version:
            raise ReleaseError("Release version is missing")
        value = self._request(
            self.RELEASES + "/download/v" + urllib.parse.quote(version, safe="") + "/" + urllib.parse.quote(name, safe=""),
            accept="application/octet-stream",
        )
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
