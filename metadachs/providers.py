"""Sonarr file identity and direct TVDB translations. Never use Sonarr prose."""

import json
import time
import urllib.error
import urllib.request

from .config import path_suffix
from .metadata import IdentityError


def request_json(url, headers, payload=None):
    request = urllib.request.Request(url, headers={**headers, "Accept": "application/json", "Content-Type": "application/json"},
                                     data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        if status == 404:
            raise IdentityError("Sonarr/TVDB-Ressource nicht gefunden; Zuordnung prüfen") from None
        raise RuntimeError(f"Sonarr/TVDB HTTP {status}") from None
    except (OSError, ValueError):
        raise RuntimeError("Sonarr/TVDB-Verbindung oder Antwort fehlerhaft") from None


class Sonarr:
    def __init__(self, config):
        self.config = config
        self.cache = {}

    def get(self, endpoint):
        if not self.config.sonarr_url or not self.config.sonarr_api_key:
            raise IdentityError("Sonarr-Adresse und API-Key für Dateizuordnung in der WebUI hinterlegen")
        cached = self.cache.get(endpoint)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        data = request_json(self.config.sonarr_url.rstrip("/") + "/api/v3/" + endpoint,
                            {"X-Api-Key": self.config.sonarr_api_key})
        if len(self.cache) >= 128:
            self.cache.clear()
        self.cache[endpoint] = (time.monotonic() + 60, data)
        return data

    def identify(self, library, path):
        if not library.sonarr_path:
            raise IdentityError("Sonarr-Pfadpräfix der Mediathek fehlt")
        target = library.sonarr_path.replace("\\", "/").rstrip("/") + "/" + path.relative_to(library.path).as_posix()
        candidates = [series for series in self.get("series") if path_suffix(target, series.get("path", "")) is not None]
        if len(candidates) != 1:
            raise IdentityError("Datei gehört nicht eindeutig zu einer Sonarr-Serie")
        series = candidates[0]
        sid = int(series["id"])
        files = [item for item in self.get(f"episodefile?seriesId={sid}") if path_suffix(item.get("path", ""), target) == ""]
        if len(files) != 1:
            raise IdentityError("Datei nicht eindeutig in Sonarr gefunden")
        episodes = [item for item in self.get(f"episode?seriesId={sid}") if item.get("episodeFileId") == files[0]["id"]]
        if len(episodes) != 1 or not episodes[0].get("tvdbId") or not series.get("tvdbId"):
            raise IdentityError("Keine eindeutige Sonarr-TVDB-Episoden-ID (Mehrfachepisoden nicht unterstützt)")
        return str(series["tvdbId"]), str(episodes[0]["tvdbId"])


class TVDB:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.token = ""
        self.expires = 0

    def get(self, endpoint):
        if not self.config.tvdb_api_key:
            raise IdentityError("TVDB-API-Key in der WebUI hinterlegen")
        key = "tvdb:" + endpoint
        cached = self.store.get_cache(key)
        if cached is not None:
            return cached
        if time.monotonic() >= self.expires:
            payload = {"apikey": self.config.tvdb_api_key}
            if self.config.tvdb_pin:
                payload["pin"] = self.config.tvdb_pin
            response = request_json("https://api4.thetvdb.com/v4/login", {}, payload)
            self.token = response["data"]["token"]
            self.expires = time.monotonic() + 86400
        try:
            response = request_json("https://api4.thetvdb.com/v4/" + endpoint, {"Authorization": "Bearer " + self.token})
        except RuntimeError:
            self.expires = 0
            raise
        data = response.get("data")
        if not isinstance(data, dict):
            raise IdentityError("TVDB lieferte keinen gültigen Datensatz")
        self.store.cache(key, data, self.config.cache_seconds)
        return data

    @staticmethod
    def language(value):
        mapping = {"de": "deu", "en": "eng", "fr": "fra", "es": "spa", "it": "ita", "ja": "jpn"}
        code = value.split("-")[0].lower()
        if code in mapping:
            return mapping[code]
        if len(code) == 3 and code.isalpha():
            return code
        raise IdentityError("TVDB-Sprache nicht unterstützt; de-DE, en-US oder dreistelligen TVDB-Sprachcode verwenden")

    def details(self, kind, identifier):
        if not str(identifier).isdigit() or int(identifier) < 1:
            raise IdentityError("Ungültige TVDB-ID")
        base = self.get(f"{kind}/{identifier}/extended")
        # Do not silently copy untranslated English base-record text.
        name = overview = ""
        for language in dict.fromkeys(filter(None, (self.config.language, self.config.fallback_language))):
            try:
                translation = self.get(f"{kind}/{identifier}/translations/{self.language(language)}")
            except IdentityError as exc:
                if "Ressource nicht gefunden" not in str(exc):
                    raise
                continue
            name = name or translation.get("name") or ""
            overview = overview or translation.get("overview") or ""
            if name and overview:
                break
        if not name:
            raise IdentityError("Kein TVDB-Titel in gewünschter Sprache/Ersatzsprache vorhanden")
        return base, {"name": name, "overview": overview, "runtime": base.get("runtime"),
                      "status": (base.get("status") or {}).get("name", "") if isinstance(base.get("status"), dict) else base.get("status", ""),
                      "air_date": base.get("aired"), "first_air_date": base.get("firstAired"),
                      "genres": base.get("genres", [])}