"""Plex paths are mapped explicitly, never guessed from titles."""

import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import path_suffix


class Plex:
    def __init__(self, config):
        self.config = config

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
        keys, parent_keys = set(), set()
        start = 0
        while True:
            page = ET.fromstring(self.request(endpoint, type=4 if library.kind == "tv" else 1,
                                              **{"X-Plex-Container-Start": start, "X-Plex-Container-Size": 200}))
            entries = list(page.findall("Video"))
            for video in entries:
                if any(path_suffix(part.get("file", ""), target) == "" for part in video.findall("./Media/Part")):
                    key = video.get("ratingKey", "")
                    if key.isdigit():
                        keys.add(key)
                    parent = video.get("grandparentRatingKey", "")
                    if parent.isdigit():
                        parent_keys.add(parent)
            start += len(entries)
            if not entries or start >= int(page.get("totalSize", str(start))):
                break
        if not keys:
            folder = target.rsplit("/", 1)[0]
            self.request(f"/library/sections/{library.plex_section}/refresh", method="POST", path=folder)
            raise RuntimeError("Plex-Scan angefordert; Eintrag noch nicht vorhanden")
        for key in sorted(parent_keys | keys):
            self.request(f"/library/metadata/{key}/refresh", method="PUT")