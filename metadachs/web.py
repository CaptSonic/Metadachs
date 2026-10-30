"""Read-only inventory and validated, atomic configuration editing for the Web UI."""

import dataclasses
import json
import logging
import os
import tempfile
import threading
import time
import tomllib
from pathlib import Path

from .config import Library, contained, load
from .metadata import EXTRAS, VIDEO_EXTENSIONS, identities, read_nfo

GROUPS = {
    "Titel": ("title", "originaltitle", "sorttitle"),
    "Beschreibung": ("plot", "outline", "tagline"),
    "IDs": ("uniqueid", "id", "episodeguide"),
    "Veröffentlichung": ("year", "premiered", "aired", "releasedate"),
    "Genres & Tags": ("genre", "tag", "country"),
    "Besetzung": ("actor",),
    "Regie & Drehbuch": ("director", "credits"),
    "Bewertungen": ("ratings", "rating", "criticrating", "userrating"),
    "Altersfreigabe": ("mpaa", "certification"),
    "Bilder": ("thumb", "fanart"),
    "Technische Daten": ("fileinfo", "runtime"),
    "Serienangaben": ("season", "episode", "status", "displayseason", "displayepisode"),
    "Studio & Sammlung": ("studio", "set"),
    "Trailer": ("trailer",),
    "Wiedergabestatus": ("watched", "playcount", "lastplayed"),
}
EDITABLE = ("language", "fallback_language", "dry_run", "replace_existing", "polling_enabled", "ui_auth_enabled", "sonarr_url", "poll_seconds",
            "stable_seconds", "retry_seconds", "cache_seconds", "plex_url")
SECRETS = ("tmdb_token", "plex_token", "webhook_token", "sonarr_api_key", "tvdb_api_key", "tvdb_pin")
LIB_FIELDS = tuple(field.name for field in dataclasses.fields(Library))
LOG = logging.getLogger("metadachs")


def inspect_nfo(path, root):
    result = {"path": str(path), "status": "missing", "title": "", "fields": {}, "groups": [], "ids": {}}
    try:
        if not contained(path, root):
            raise ValueError("NFO außerhalb des Medienordners")
        xml = read_nfo(path)
        if xml is None:
            return result
        records = list(xml) if xml.tag == "episodes" else [xml]
        fields = {}
        for record in records:
            for child in record:
                if not isinstance(child.tag, str):
                    continue
                values = [text.strip() for text in child.itertext() if text.strip()]
                if values:
                    fields.setdefault(child.tag, []).append(" · ".join(values)[:2000])
        record_ids = [identities([], record) for record in records]
        result.update(status="valid", title=" / ".join(record.findtext("title") or "" for record in records), fields=fields,
                      groups=[name for name, tags in GROUPS.items() if any(tag in fields for tag in tags)],
                      ids=record_ids[0] if len(records) == 1 else {},
                      episodes=[{"season": record.findtext("season"), "episode": record.findtext("episode"),
                                 "title": record.findtext("title") or "", "ids": ids}
                                for record, ids in zip(records, record_ids)] if xml.tag == "episodes" else [])
    except (OSError, ValueError, UnicodeError) as exc:
        result.update(status="error", error=str(exc))
    return result


class Web:
    def __init__(self, service, config_path=None):
        self.service = service
        self.config_path = config_path
        self.lock = threading.Lock()
        self.config_lock = threading.Lock()
        self.items = []
        self.errors = []
        self.updated = 0
        self.scanning = False
        self.restart_required = False
        self.thread = None
        self.nfo_thread = None
        self.nfo_updating = False

    def refresh(self):
        with self.lock:
            if self.scanning:
                return False
            self.scanning = True
            self.thread = threading.Thread(target=self._build, daemon=True)
            self.thread.start()
        return True

    def _build(self):
        items, errors = {}, []
        started = time.monotonic()
        LOG.info("WebUI-Bestandsabgleich gestartet (nur lesend)")
        try:
            for lib in self.service.config.libraries:
                if not lib.path.is_dir():
                    errors.append(f"Mount nicht verfügbar: {lib.name}")
                    continue
                def onerror(error):
                    errors.append(f"Ordner nicht lesbar: {error.filename}")
                for folder, dirs, files in os.walk(lib.path, followlinks=False, onerror=onerror):
                    dirs[:] = [name for name in dirs if name.lower() not in EXTRAS and not Path(folder, name).is_symlink()]
                    for name in files:
                        path = Path(folder, name)
                        try:
                            if not self.service.eligible(path, lib):
                                continue
                            relative = path.relative_to(lib.path)
                            home = lib.path / relative.parts[0] if len(relative.parts) > 1 else path.parent
                            key = (lib.name, str(home))
                            if key not in items:
                                nfo = inspect_nfo(home / ("tvshow.nfo" if lib.kind == "tv" else "movie.nfo"), lib.path)
                                items[key] = {"library": lib.name, "kind": lib.kind, "path": str(home),
                                              "title": nfo["title"] or home.name, "nfo": nfo, "files": []}
                            entry = {"path": str(path), "name": name}
                            if lib.kind == "tv":
                                entry["nfo"] = inspect_nfo(path.with_suffix(".nfo"), lib.path)
                            items[key]["files"].append(entry)
                        except (OSError, ValueError) as exc:
                            errors.append(f"Datei nicht lesbar: {path}: {type(exc).__name__}")
            with self.lock:
                self.items = sorted(items.values(), key=lambda item: (item["library"], item["title"].casefold()))
                self.errors, self.updated = errors, time.time()
            LOG.info("WebUI-Bestandsabgleich beendet: %d Filme/Serien, %d Videos, %d Fehler (%.1f s)",
                     len(items), sum(len(item["files"]) for item in items.values()), len(errors), time.monotonic() - started)
        finally:
            with self.lock:
                self.scanning = False

    def inventory(self, query="", library="", offset=0, limit=50):
        with self.lock:
            items = [item for item in self.items if (not library or item["library"] == library)
                     and query.casefold() in (item["title"] + " " + item["path"]).casefold()]
            return {"items": items[offset:offset + limit], "total": len(items), "offset": offset,
                    "updated": self.updated, "scanning": self.scanning, "errors": self.errors,
                    "libraries": [lib.name for lib in self.service.config.libraries]}

    def update_nfos(self, payload):
        """Only accept known inventory targets; never arbitrary browser paths."""
        if "all" in payload:
            if payload != {"all": True}:
                raise ValueError("Globale Aktualisierung benötigt ausschließlich all=true")
            with self.lock:
                if self.nfo_updating:
                    return {"started": False, "running": True, "dry_run": self.service.config.dry_run}
                self.nfo_updating = True
                self.nfo_thread = threading.Thread(target=self._update_all_nfos, daemon=True)
                self.nfo_thread.start()
            return {"started": True, "running": True, "dry_run": self.service.config.dry_run}
        target, library = payload.get("path", ""), payload.get("library", "")
        if not isinstance(target, str) or not isinstance(library, str):
            raise ValueError("Pfad und Mediathek müssen Text sein")
        if not target and not library:
            raise ValueError("Datei/Serie oder Mediathek auswählen")
        with self.lock:
            items = [item for item in self.items if not library or item["library"] == library]
            files = [file["path"] for item in items for file in item["files"]
                     if not target or target in (item["path"], file["path"])]
        if not files:
            raise ValueError("Kein passender Bestand; zuerst Bestand neu prüfen")
        return self._queue_nfo_paths(files)

    def _update_all_nfos(self):
        """Discover every configured library, independently of cached UI pages."""
        LOG.info("Globale NFO-Aktualisierung gestartet: alle konfigurierten Mediatheken; nur fehlende IDs")
        errors = []

        def paths():
            for lib in self.service.config.libraries:
                if self.service.stop.is_set():
                    return
                if not lib.path.is_dir():
                    errors.append(f"Mount nicht verfügbar: {lib.name}")
                    continue
                def onerror(error):
                    errors.append(f"Ordner nicht lesbar: {lib.name}: {error.filename}")
                for folder, dirs, files in os.walk(lib.path, followlinks=False, onerror=onerror):
                    dirs[:] = [name for name in dirs if name.lower() not in EXTRAS and not Path(folder, name).is_symlink()]
                    for name in files:
                        if self.service.stop.is_set():
                            return
                        path = Path(folder, name)
                        if path.suffix.lower() in VIDEO_EXTENSIONS:
                            yield str(path)
        try:
            result = self._queue_nfo_paths(paths(), errors)
            for error in errors:
                LOG.warning("Globale NFO-Aktualisierung: %s", error)
            LOG.info("Globale NFO-Aktualisierung: Erfassung %s; %d Aufträge eingereiht, %d übersprungen, %d Fehler. Verarbeitung unter Aufträge/Log verfolgen.",
                     "abgebrochen" if self.service.stop.is_set() else "abgeschlossen",
                     result["queued"], result["skipped"], len(errors))
        except Exception as exc:
            LOG.warning("Globale NFO-Aktualisierung abgebrochen: %s", type(exc).__name__)
        finally:
            with self.lock:
                self.nfo_updating = False

    def _queue_nfo_paths(self, files, errors=None):
        queued = skipped = 0
        for name in files:
            try:
                accepted = self._queue_nfo_path(Path(name))
            except (OSError, ValueError) as exc:
                if errors is None:
                    raise
                errors.append(f"Datei nicht prüfbar: {name}: {type(exc).__name__}")
                skipped += 1
                continue
            queued += int(accepted)
            skipped += int(not accepted)
        LOG.info("NFO-Aktualisierung angefordert: %d Aufträge, %d übersprungen; nur fehlende IDs", queued, skipped)
        return {"queued": queued, "skipped": skipped, "dry_run": self.service.config.dry_run}

    def _queue_nfo_path(self, path):
        lib = self.service.library(path)
        if not self.service.eligible(path, lib):
            return False
        if lib.kind == "tv":
            nfo = path.with_suffix(".nfo")
        else:
            nfo = path.parent / "movie.nfo"
        relative = path.relative_to(lib.path)
        home = lib.path / relative.parts[0] if len(relative.parts) > 1 else path.parent
        if not nfo.exists() and not (lib.kind == "tv" and (home / "tvshow.nfo").exists()):
            return False
        return self.service.store.enqueue_nfo_update(lib.name, str(path), self.service.signature(path))

    def settings(self):
        # Return effective settings, but never expose saved or environment secrets.
        config = self.service.config
        raw = tomllib.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path else {}
        return {"settings": {key: raw.get("settings", {}).get(key, getattr(config, key)) for key in EDITABLE},
                "libraries": [{key: str(getattr(lib, key)) if key == "path" else getattr(lib, key)
                               for key in LIB_FIELDS} for lib in config.libraries] if not raw else raw.get("libraries", []),
                "secrets": {key: bool(getattr(config, key)) for key in SECRETS},
                "environment_overrides": [key for key in (*EDITABLE, *SECRETS)
                                          if "METADACHS_" + key.upper() in os.environ],
                "restart_required": self.restart_required, "writable": self.config_path is not None}

    def save(self, payload):
        if self.config_path is None:
            raise ValueError("Konfigurationspfad nicht verfügbar")
        if set(payload) - {"settings", "libraries", "secrets"}:
            raise ValueError("Unbekannte Konfigurationsfelder")
        with self.config_lock:
            original = self.config_path.read_text(encoding="utf-8")
            raw = tomllib.loads(original)
            settings = payload.get("settings", {})
            secrets = payload.get("secrets", {})
            if not isinstance(settings, dict) or set(settings) - set(EDITABLE):
                raise ValueError("Ungültige Einstellungen")
            if not isinstance(secrets, dict) or set(secrets) - set(SECRETS):
                raise ValueError("Ungültige Zugangsdaten")
            raw.setdefault("settings", {}).update(settings)
            for key, value in secrets.items():
                if not isinstance(value, str):
                    raise ValueError("Token muss Text sein")
                if value:
                    raw["settings"][key] = value
            if "libraries" in payload:
                if not isinstance(payload["libraries"], list):
                    raise ValueError("Mediatheken müssen eine Liste sein")
                raw["libraries"] = payload["libraries"]

            def literal(value):
                if type(value) is bool:
                    return str(value).lower()
                if type(value) is int:
                    return str(value)
                if isinstance(value, str):
                    # Literal TOML strings preserve ${...} and backslashes.
                    return json.dumps(value, ensure_ascii=False)
                raise ValueError("Nicht unterstützter Konfigurationswert")

            lines = ["# Durch Metadachs WebUI gespeichert; vorherige Version als .bak gesichert.", "[settings]"]
            lines.extend(f"{key} = {literal(value)}" for key, value in raw["settings"].items())
            for lib in raw.get("libraries", []):
                if not isinstance(lib, dict) or set(lib) - set(LIB_FIELDS):
                    raise ValueError("Ungültige Mediathek")
                lines.append("\n[[libraries]]")
                lines.extend(f"{key} = {literal(value)}" for key, value in lib.items())
            content = "\n".join(lines) + "\n"
            fd, name = tempfile.mkstemp(prefix=".metadachs-config-", dir=self.config_path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                validated = load(Path(name))
                self.service.logs.add_secrets(getattr(validated, key) for key in SECRETS)
                if len(validated.webhook_token) < 16:
                    raise ValueError("Webhook-Token muss mindestens 16 Zeichen lang sein")
                # Preserve exact previous bytes and restrict token-containing backups.
                backup = self.config_path.with_name(self.config_path.name + f".{time.time_ns()}.bak")
                with backup.open("x", encoding="utf-8") as handle:
                    os.chmod(backup, 0o600)
                    handle.write(original)
                os.replace(name, self.config_path)
                self.restart_required = True
                LOG.info("WebUI-Einstellungen gespeichert und gesichert; Container-Neustart erforderlich")
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return {"saved": True, "restart_required": True}