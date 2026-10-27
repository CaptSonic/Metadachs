"""Plex paths are mapped explicitly, never guessed from titles."""

import urllib.error
import urllib.parse
import urllib.request
import time
import re
import xml.etree.ElementTree as ET
from pathlib import Path

class Plex:
    def __init__(self, config):
        self.config = config
        self.indexes = {}

    @staticmethod
    def path_key(value):
        value = value.replace("\\", "/")
        return value.casefold() if re.match(r"^[A-Za-z]:", value) else value

    def request(self, endpoint, method="GET", **params):
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(self.config.plex_url.rstrip("/") + endpoint + ("?" + query if query else ""),
                                        method=method, headers={"X-Plex-Token": self.config.plex_token,
                                                                "Accept": "application/xml"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Plex HTTP {exc.code}") from None
        except OSError:
            raise RuntimeError("Plex nicht erreichbar") from None

    def refresh(self, library, media: Path):
        if not library.plex_section or not self.config.plex_url or not self.config.plex_token:
            raise RuntimeError("Plex-Aktualisierung ist unvollständig konfiguriert")
        relative = media.relative_to(library.path).as_posix()
        target = library.plex_path.replace("\\", "/").rstrip("/") + "/" + relative
        endpoint = f"/library/sections/{library.plex_section}/all"
        cache_key = (library.plex_section, library.kind)
        cached = self.indexes.get(cache_key)
        if cached is None or time.monotonic() >= cached[0]:
            index = {}
            start = 0
            while True:
                page = ET.fromstring(self.request(endpoint, type=4 if library.kind == "tv" else 1,
                                                  **{"X-Plex-Container-Start": start, "X-Plex-Container-Size": 200}))
                entries = list(page.findall("Video"))
                for video in entries:
                    key = video.get("ratingKey", "")
                    parent = video.get("grandparentRatingKey", "")
                    for part in video.findall("./Media/Part"):
                        path = self.path_key(part.get("file", ""))
                        keys, parent_keys = index.setdefault(path, (set(), set()))
                        if key.isdigit():
                            keys.add(key)
                        if parent.isdigit():
                            parent_keys.add(parent)
                start += len(entries)
                if not entries or start >= int(page.get("totalSize", str(start))):
                    break
            self.indexes[cache_key] = (time.monotonic() + 60, index)
        else:
            index = cached[1]
        keys, parent_keys = index.get(self.path_key(target), (set(), set()))
        if not keys:
            folder = target.rsplit("/", 1)[0]
            self.request(f"/library/sections/{library.plex_section}/refresh", method="POST", path=folder)
            raise RuntimeError("Plex-Scan angefordert; Eintrag noch nicht vorhanden")
        for key in sorted(parent_keys | keys):
            self.request(f"/library/metadata/{key}/refresh", method="PUT")