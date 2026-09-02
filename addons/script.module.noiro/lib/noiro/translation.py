import hashlib
import json
import os
import urllib.error
import urllib.request

from .security import _atomic_json_write


class GeminiTranslator(object):
    """Small fail-soft subtitle translator with an on-disk per-line cache."""

    API = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"

    def __init__(self, api_key, cache_path, model="gemini-2.5-flash", timeout=20, opener=None):
        self.api_key = api_key
        self.cache_path = cache_path
        self.model = model
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    def _cache(self):
        try:
            with open(self.cache_path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def translate(self, text, target_language):
        original = str(text or "")
        if not original.strip() or not self.api_key:
            return {"text": original, "translated": False, "fallback": True}
        key = hashlib.sha256((self.model + "\0" + target_language + "\0" + original).encode("utf-8")).hexdigest()
        cache = self._cache()
        if key in cache:
            return {"text": cache[key], "translated": True, "cached": True}
        prompt = (
            "Translate this subtitle text to %s. Preserve line breaks, names, timing meaning, "
            "and any basic formatting. Return only the translated subtitle text:\n\n%s"
        ) % (target_language, original)
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1},
        }).encode("utf-8")
        request = urllib.request.Request(
            self.API % self.model,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-goog-api-key": self.api_key,
                "User-Agent": "Noiro-Kodi/0.1",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            translated = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
            if not translated:
                raise ValueError("empty translation")
            cache[key] = translated
            if len(cache) > 5000:
                cache = dict(list(cache.items())[-4000:])
            _atomic_json_write(self.cache_path, cache)
            return {"text": translated, "translated": True, "cached": False}
        except (OSError, ValueError, KeyError, IndexError, urllib.error.URLError):
            return {"text": original, "translated": False, "fallback": True}

    def translate_document(self, text, target_language):
        original = str(text or "")
        # Oversized subtitle files remain on their original track instead of
        # delaying playback or exceeding the model request limit.
        if len(original.encode("utf-8")) > 350000:
            return {"text": original, "translated": False, "fallback": True}
        result = self.translate(original, target_language)
        translated = result.get("text") or ""
        if translated.startswith("```") and translated.endswith("```"):
            lines = translated.splitlines()
            translated = "\n".join(lines[1:-1])
        original_cues = original.count("-->")
        translated_cues = translated.count("-->")
        if original_cues and translated_cues != original_cues:
            return {"text": original, "translated": False, "fallback": True}
        result["text"] = translated
        return result
