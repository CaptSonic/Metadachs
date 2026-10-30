"""Sonarr file identity and direct TVDB translations. Never use Sonarr prose."""

import json
import copy
from collections import OrderedDict
import re
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
        self.file_indexes = {}
        self.episode_indexes = {}

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
        series, episodes = self.identify_many(library, path)
        if len(episodes) != 1:
            raise IdentityError("Keine eindeutige einzelne Sonarr-TVDB-Episoden-ID")
        return series, episodes[0]

    def identify_many(self, library, path):
        if not library.sonarr_path:
            raise IdentityError("Sonarr-Pfadpräfix der Mediathek fehlt")
        target = library.sonarr_path.replace("\\", "/").rstrip("/") + "/" + path.relative_to(library.path).as_posix()
        candidates = [series for series in self.get("series") if path_suffix(target, series.get("path", "")) is not None]
        if len(candidates) != 1:
            raise IdentityError("Datei gehört nicht eindeutig zu einer Sonarr-Serie")
        series = candidates[0]
        sid = int(series["id"])
        file_list = self.get(f"episodefile?seriesId={sid}")
        key = target.casefold() if re.match(r"^[A-Za-z]:", target) else target
        cached = self.file_indexes.get(sid)
        if cached is None or cached[0] is not file_list:
            index = {}
            for item in file_list:
                name = item.get("path", "").replace("\\", "/")
                name = name.casefold() if re.match(r"^[A-Za-z]:", name) else name
                index.setdefault(name, []).append(item)
            if len(self.file_indexes) >= 64:
                self.file_indexes.clear()
            self.file_indexes[sid] = (file_list, index)
        files = self.file_indexes[sid][1].get(key, [])
        if len(files) != 1:
            raise IdentityError("Datei nicht eindeutig in Sonarr gefunden")
        episode_list = self.get(f"episode?seriesId={sid}")
        cached = self.episode_indexes.get(sid)
        if cached is None or cached[0] is not episode_list:
            index = {}
            for item in episode_list:
                index.setdefault(item.get("episodeFileId"), []).append(item)
            if len(self.episode_indexes) >= 64:
                self.episode_indexes.clear()
            self.episode_indexes[sid] = (episode_list, index)
        episodes = self.episode_indexes[sid][1].get(files[0]["id"], [])
        if not episodes or not series.get("tvdbId") or any(not item.get("tvdbId") for item in episodes):
            raise IdentityError("Keine eindeutigen Sonarr-TVDB-Episoden-IDs")
        if len(episodes) > 1:
            locations = [(item.get("seasonNumber"), item.get("episodeNumber")) for item in episodes]
            if any(type(s) is not int or type(e) is not int for s, e in locations) or len(set(locations)) != len(locations):
                raise IdentityError("Sonarr-Episodenreihenfolge nicht eindeutig")
            if len({s for s, e in locations}) != 1:
                raise IdentityError("Staffelübergreifende Sonarr-Dateizuordnung nicht unterstützt")
            episodes = sorted(episodes, key=lambda item: item["episodeNumber"])
        ids = [str(item["tvdbId"]) for item in episodes]
        if len(set(ids)) != len(ids):
            raise IdentityError("Doppelte Sonarr-TVDB-Episoden-ID")
        return str(series["tvdbId"]), ids


class TVDB:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.token = ""
        self.expires = 0
        self.memory = OrderedDict()

    def get(self, endpoint):
        if not self.config.tvdb_api_key:
            raise IdentityError("TVDB-API-Key in der WebUI hinterlegen")
        key = "tvdb:" + endpoint
        entry = self.memory.get(key)
        if entry and time.monotonic() < entry[0]:
            self.memory.move_to_end(key)
            return copy.deepcopy(entry[1])
        cached = self.store.get_cache(key)
        if cached is not None:
            self.remember(key, cached)
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
        self.remember(key, data)
        return data

    def remember(self, key, data):
        self.memory[key] = (time.monotonic() + min(60, self.config.cache_seconds), copy.deepcopy(data))
        self.memory.move_to_end(key)
        while len(self.memory) > 512:
            self.memory.popitem(last=False)

    @staticmethod
    def external_ids(base):
        """Use explicit provider links only, never infer IDs from numbering."""
        result = {}
        for item in base.get("remoteIds") or []:
            source = re.sub(r"[^a-z0-9]", "", str(item.get("sourceName", "")).lower())
            provider = {"imdb": "imdb", "themoviedb": "tmdb", "themoviedbcom": "tmdb",
                        "themoviedborg": "tmdb", "tmdb": "tmdb"}.get(source)
            if provider is None:
                continue
            value = str(item.get("id") or "").strip()
            if value in ("", "0"):
                continue
            if not re.fullmatch(r"tt\d+" if provider == "imdb" else r"[1-9]\d*", value):
                raise IdentityError(f"Ungültige externe TVDB-{provider}-ID")
            key = provider + "_id"
            if key in result and result[key] != value:
                raise IdentityError(f"Widersprüchliche externe TVDB-{provider}-IDs")
            result[key] = value
        return result

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
                      "external_ids": self.external_ids(base),
                      "status": (base.get("status") or {}).get("name", "") if isinstance(base.get("status"), dict) else base.get("status", ""),
                      "air_date": base.get("aired"), "first_air_date": base.get("firstAired"),
                      "genres": base.get("genres", [])}