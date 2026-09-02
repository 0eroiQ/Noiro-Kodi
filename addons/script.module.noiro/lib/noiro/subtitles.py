import hashlib
import ipaddress
import os
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request


class SubtitleError(RuntimeError):
    pass


class SubtitleCache(object):
    MAX_SIZE = 4 * 1024 * 1024

    def __init__(self, directory, timeout=10, opener=None):
        self.directory = directory
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen
        os.makedirs(directory, mode=0o700, exist_ok=True)

    @staticmethod
    def _public_host(hostname):
        try:
            addresses = socket.getaddrinfo(hostname, None)
        except socket.gaierror as error:
            raise SubtitleError("Subtitle host could not be resolved: %s" % error)
        for address in addresses:
            value = ipaddress.ip_address(address[4][0].split("%", 1)[0])
            if value.is_private or value.is_loopback or value.is_link_local or value.is_multicast or value.is_unspecified:
                raise SubtitleError("Private subtitle hosts are not allowed")

    @staticmethod
    def _extension(url, content_type):
        suffix = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if suffix in (".srt", ".vtt", ".ass", ".ssa", ".sub"):
            return suffix
        if "vtt" in str(content_type or "").lower():
            return ".vtt"
        return ".srt"

    def download(self, candidate):
        url = str(candidate.get("url") or "")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise SubtitleError("Subtitle URL must use HTTP or HTTPS")
        self._public_host(parsed.hostname)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        known = [os.path.join(self.directory, digest + extension) for extension in (".srt", ".vtt", ".ass", ".ssa", ".sub")]
        for path in known:
            if os.path.isfile(path) and 0 < os.path.getsize(path) <= self.MAX_SIZE:
                return path
        request = urllib.request.Request(
            url,
            headers={"Accept": "text/plain, application/x-subrip, text/vtt", "User-Agent": "Noiro-Kodi/0.1"},
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                payload = response.read(self.MAX_SIZE + 1)
                content_type = response.headers.get("Content-Type")
        except (OSError, urllib.error.URLError) as error:
            raise SubtitleError("Subtitle download failed: %s" % error)
        if not payload or len(payload) > self.MAX_SIZE:
            raise SubtitleError("Subtitle is empty or too large")
        extension = self._extension(url, content_type)
        path = os.path.join(self.directory, digest + extension)
        descriptor, temporary = tempfile.mkstemp(prefix=".subtitle-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path

    def translated_path(self, source, language):
        digest = hashlib.sha256((source + "\0" + language).encode("utf-8")).hexdigest()
        extension = os.path.splitext(source)[1] or ".srt"
        return os.path.join(self.directory, digest + ".translated" + extension)

    @staticmethod
    def read_text(path):
        with open(path, "rb") as handle:
            payload = handle.read(SubtitleCache.MAX_SIZE + 1)
        if len(payload) > SubtitleCache.MAX_SIZE:
            raise SubtitleError("Subtitle is too large")
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise SubtitleError("Subtitle encoding is not supported")

    @staticmethod
    def write_text(path, text):
        directory = os.path.dirname(path)
        descriptor, temporary = tempfile.mkstemp(prefix=".translation-", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
