"""TMDB client, conservative identity resolution and Kodi/Plex XML."""

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".m2ts", ".mov", ".wmv"}
EXTRAS = {"extras", "trailers", "behind the scenes", "deleted scenes", "featurettes", "interviews", "scenes", "shorts", "other", "samples"}


class IdentityError(ValueError):
    pass


def read_nfo(path: Path):
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
        raise IdentityError(f"Unsichere oder zu große NFO: {path}")
    text = path.read_text(encoding="utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise IdentityError(f"DTD/Entities sind nicht erlaubt: {path}")
    # Some managers append a scraper URL after the XML root. Read it, never
    # silently repair the original file.
    match = re.search(r"</(movie|tvshow|episodedetails)>\s*(https?://[^\r\n]+\s*)?$", text)
    if match:
        text = text[:text.index("</" + match.group(1) + ">", match.start()) + len(match.group(1)) + 3]
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise IdentityError(f"Ungültige NFO: {path}") from exc


def identities(names, nfo=None):
    result = {}

    def add(kind, value):
        value = str(value).strip()
        if not value or value in ("0", "None"):
            return
        if kind not in ("tmdb", "tvdb", "imdb"):
            return
        if not re.fullmatch(r"tt\d+" if kind == "imdb" else r"[1-9]\d*", value):
            raise IdentityError(f"Ungültige {kind}-ID")
        if kind in result and result[kind] != value:
            raise IdentityError(f"Widersprüchliche {kind}-IDs")
        result[kind] = value

    for name in names:
        for kind, value in re.findall(r"\{(tmdb|tvdb|imdb)-([^}]+)\}", name, re.I):
            add(kind.lower(), value)
    if nfo is not None:
        for node in nfo.findall("uniqueid"):
            add(node.get("type", "").lower(), node.text or "")
    return result


class TMDB:
    def __init__(self, config, store):
        self.config, self.store = config, store

    def get(self, endpoint, **params):
        if not self.config.tmdb_token:
            raise RuntimeError("METADACHS_TMDB_TOKEN fehlt")
        params.setdefault("language", self.config.language)
        query = urllib.parse.urlencode(sorted(params.items()))
        key = endpoint + "?" + query
        cached = self.store.get_cache(key)
        if cached is not None:
            return cached
        request = urllib.request.Request("https://api.themoviedb.org/3/" + key,
                                        headers={"Authorization": "Bearer " + self.config.tmdb_token,
                                                 "Accept": "application/json", "User-Agent": "Metadachs/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"TMDB HTTP {exc.code}") from None
        except (OSError, ValueError):
            raise RuntimeError("TMDB-Verbindung oder Antwort fehlerhaft") from None
        self.store.cache(key, data, self.config.cache_seconds)
        return data

    def details(self, endpoint, append="credits,external_ids"):
        data = self.get(endpoint, append_to_response=append)
        if self.config.fallback_language and self.config.fallback_language != self.config.language:
            if not data.get("overview") or not (data.get("title") or data.get("name")):
                fallback = self.get(endpoint, append_to_response=append, language=self.config.fallback_language)
                data = dict(data)
                for key in ("overview", "title", "name", "tagline"):
                    if not data.get(key) and fallback.get(key):
                        data[key] = fallback[key]
        return data

    def resolve(self, ids, kind):
        candidates = set()
        if ids.get("tmdb"):
            candidates.add(int(ids["tmdb"]))
        for source in ("imdb", "tvdb"):
            if source not in ids or (kind == "movie" and source == "tvdb"):
                continue
            found = self.get(f"find/{ids[source]}", external_source=source + "_id").get(kind + "_results", [])
            if len(found) != 1:
                raise IdentityError(f"{source}-ID nicht eindeutig bei TMDB gefunden")
            candidates.add(int(found[0]["id"]))
        if len(candidates) != 1:
            raise IdentityError("Fehlende oder widersprüchliche TMDB-Zuordnung")
        return candidates.pop()

    def episode(self, show_id, ids, season, number, allow_numbering):
        location = None
        for source in ("tvdb", "imdb"):
            if source not in ids:
                continue
            found = self.get(f"find/{ids[source]}", external_source=source + "_id").get("tv_episode_results", [])
            if len(found) != 1 or int(found[0]["show_id"]) != show_id:
                raise IdentityError("Episoden-ID passt nicht eindeutig zur Serie")
            item = found[0]
            current = (int(item["season_number"]), int(item["episode_number"]))
            if location and location != current:
                raise IdentityError("Widersprüchliche Episoden-IDs")
            location = current
        if location is None:
            if not allow_numbering:
                raise IdentityError("Keine externe Episoden-ID; season_numbering muss bewusst aktiviert werden")
            location = (season, number)
        data = self.details(f"tv/{show_id}/season/{location[0]}/episode/{location[1]}")
        if ids.get("tmdb") and str(data["id"]) != ids["tmdb"]:
            raise IdentityError("TMDB-Episoden-ID stimmt nicht überein")
        return data


def nfo_xml(data, kind, season=None, episode=None):
    root = ET.Element({"movie": "movie", "tv": "tvshow", "episode": "episodedetails"}[kind])

    def node(tag, value, parent=root, **attrs):
        if value is not None and value != "":
            ET.SubElement(parent, tag, attrs).text = str(value)

    node("title", data.get("title") or data.get("name"))
    node("originaltitle", data.get("original_title") or data.get("original_name"))
    node("plot", data.get("overview"))
    node("tagline", data.get("tagline"))
    date = data.get("release_date") or data.get("first_air_date") or data.get("air_date")
    node("aired" if kind == "episode" else "premiered", date)
    node("year", date[:4] if date else None)
    node("runtime", data.get("runtime"))
    if kind == "episode":
        node("season", season)
        node("episode", episode)
    if kind == "tv":
        node("status", "Ended" if data.get("status") in ("Ended", "Canceled") else "Continuing")
    node("uniqueid", data.get("id"), type="tmdb", default="true")
    external = data.get("external_ids", {})
    for source in ("imdb", "tvdb"):
        node("uniqueid", external.get(source + "_id") or data.get(source + "_id"), type=source)
    for genre in data.get("genres", []):
        node("genre", genre.get("name"))
    for studio in data.get("networks" if kind == "tv" else "production_companies", []):
        node("studio", studio.get("name"))
    for country in data.get("production_countries", []):
        node("country", country.get("name"))
    collection = data.get("belongs_to_collection")
    if collection:
        node("name", collection.get("name"), ET.SubElement(root, "set"))
    if data.get("vote_count", 0):
        rating = ET.SubElement(ET.SubElement(root, "ratings"), "rating", {"name": "themoviedb", "max": "10", "default": "true"})
        node("value", data.get("vote_average"), rating)
        node("votes", data.get("vote_count"), rating)
    credits = data.get("credits", {})
    for person in credits.get("crew", []):
        if person.get("job") == "Director":
            node("director", person.get("name"))
        elif person.get("job") in ("Writer", "Screenplay", "Story"):
            node("credits", person.get("name"))
    cast = credits.get("cast", []) + data.get("guest_stars", [])
    seen = set()
    for person in cast[:60]:
        identity = (person.get("id"), person.get("character"))
        if identity in seen:
            continue
        seen.add(identity)
        actor = ET.SubElement(root, "actor")
        node("name", person.get("name"), actor)
        node("role", person.get("character"), actor)
        node("order", person.get("order"), actor)
        if person.get("profile_path"):
            node("thumb", "https://image.tmdb.org/t/p/w500" + person["profile_path"], actor)
    if data.get("poster_path"):
        node("thumb", "https://image.tmdb.org/t/p/original" + data["poster_path"], aspect="poster")
    if data.get("backdrop_path"):
        node("thumb", "https://image.tmdb.org/t/p/original" + data["backdrop_path"], ET.SubElement(root, "fanart"))
    ET.indent(root, space="  ")
    content = ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"
    ET.fromstring(content)
    return content


def sha(content):
    return hashlib.sha256(content).hexdigest()


def write_nfo(path, content, config, store):
    """Never take ownership of an unchanged foreign file; back up replacements."""
    if path.is_symlink():
        raise IdentityError("NFO-Symlinks werden nicht beschrieben")
    old = path.read_bytes() if path.exists() else None
    if old == content:
        return "unchanged"
    if old is not None and not config.replace_existing and store.digest(path) != sha(old):
        return "preserved"
    if config.dry_run:
        return "would-write"
    if old is not None:
        backup = path.with_name(path.name + f".{time.time_ns()}.bak")
        shutil.copy2(path, backup)
    fd, temporary = tempfile.mkstemp(prefix=".metadachs-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    store.remember(path, sha(content))
    return "written"