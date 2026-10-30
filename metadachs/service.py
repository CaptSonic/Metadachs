"""Polling scanner, durable job execution and authenticated Sonarr HTTP ingress."""

import base64
import dataclasses
import hmac
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .config import contained, path_suffix
from .metadata import EXTRAS, VIDEO_EXTENSIONS, IdentityError, TMDB, identities, nfo_xml, read_nfo, sha, write_nfo, episode_numbers, episode_nfos, merge_nfo_ids
from .plex import Plex
from .store import Store
from .web import Web
from .providers import Sonarr, TVDB
from .logs import WebLog

LOG = logging.getLogger("metadachs")


def preserve_external_ids(data, existing_ids):
    """Retain explicit NFO links and reject changes in provider identity."""
    external = dict(data.get("external_ids") or {})
    for source in ("tmdb", "imdb"):
        key = source + "_id"
        old = existing_ids.get(source)
        current = external.get(key)
        if old and current and str(old) != str(current):
            raise IdentityError(f"Externe {source}-ID widerspricht vorhandener NFO/Pfad-ID")
        if old:
            external[key] = old
    return {**data, "external_ids": external}


class Service:
    def __init__(self, config, recover=True, config_path=None):
        self.config = config
        self.logs = WebLog(getattr(config, key) for key in
                           ('tmdb_token', 'plex_token', 'webhook_token', 'sonarr_api_key', 'tvdb_api_key', 'tvdb_pin'))
        self.store = Store(config.state, recover=recover)
        self.tmdb = TMDB(config, self.store)
        self.plex = Plex(config)
        self.sonarr = Sonarr(config)
        self.tvdb = TVDB(config, self.store)
        self.stop = threading.Event()
        self.observed = {}
        self.scan_lock = threading.Lock()
        self.last_scan = 0.0
        self.scan_errors = []
        self.web = Web(self, config_path)

    def library(self, path):
        matches = [lib for lib in self.config.libraries if contained(path, lib.path)]
        if len(matches) != 1:
            raise IdentityError("Pfad gehört nicht eindeutig zu einer Mediathek")
        return matches[0]

    def signature(self, path):
        stat = path.stat()
        policy = f"{self.config.dry_run}:{self.config.replace_existing}:{self.config.language}:{self.config.fallback_language}"
        lib = self.library(path)
        policy += f":{lib.season_numbering}:{lib.plex_section}:{lib.plex_path}:{lib.metadata_source}:{lib.sonarr_path}:{self.config.sonarr_url}"
        return sha(f"{stat.st_size}:{stat.st_mtime_ns}:{policy}".encode())

    def eligible(self, path, library):
        return (path.suffix.lower() in VIDEO_EXTENSIONS and path.is_file()
                and not path.is_symlink() and contained(path, library.path)
                and not any(part.lower() in EXTRAS for part in path.relative_to(library.path).parts[:-1]))

    def scan(self, once=False):
        with self.scan_lock:
            started = time.monotonic()
            queued = waiting = 0
            LOG.info("Ordnerprüfung gestartet: %d Mediatheken", len(self.config.libraries))
            errors = []
            current_paths = set()
            for lib in self.config.libraries:
                found = 0
                LOG.info("Prüfe Mediathek %s: %s", lib.name, lib.path)
                if not lib.path.is_dir():
                    errors.append(f"Mount nicht verfügbar: {lib.name}")
                    continue

                def onerror(error):
                    errors.append(f"Ordner nicht lesbar: {lib.name}: {error.filename}")

                for folder, dirs, files in os.walk(lib.path, followlinks=False, onerror=onerror):
                    dirs[:] = [name for name in dirs if name.lower() not in EXTRAS and not Path(folder, name).is_symlink()]
                    for name in files:
                        path = Path(folder, name)
                        try:
                            if not self.eligible(path, lib):
                                continue
                            current_paths.add(str(path))
                            found += 1
                            if found % 500 == 0:
                                LOG.info("Ordnerprüfung %s: bisher %d Videodateien", lib.name, found)
                            signature = self.signature(path)
                            now = time.time()
                            previous = self.observed.get(str(path))
                            if previous is None or previous[0] != signature:
                                self.observed[str(path)] = (signature, now)
                            stable_since = self.observed[str(path)][1]
                            if now - path.stat().st_mtime < self.config.stable_seconds:
                                waiting += 1
                                continue
                            if once or now - stable_since >= self.config.stable_seconds:
                                queued += self.store.enqueue(lib.name, str(path), signature)
                            else:
                                waiting += 1
                        except (OSError, ValueError) as exc:
                            errors.append(f"Datei nicht prüfbar: {path}: {type(exc).__name__}")
                LOG.info("Mediathek %s geprüft: %d Videodateien", lib.name, found)
            self.observed = {key: value for key, value in self.observed.items() if key in current_paths}
            self.last_scan, self.scan_errors = time.time(), errors
            for error in errors:
                LOG.warning("%s", error)
            LOG.info("Ordnerprüfung beendet: %d Videos, %d neue/geänderte Aufträge, %d warten auf Dateiruhe, %d Fehler (%.1f s); nächste Prüfung in %d s",
                     len(current_paths), queued, waiting, len(errors), time.monotonic() - started, self.config.poll_seconds)

    def webhook(self, payload):
        event = payload.get("eventType")
        if event == "Test":
            LOG.info("Sonarr-Testwebhook empfangen und bestätigt")
            return {"accepted": True, "test": True}
        if event not in ("Download", "ImportComplete", "Rename", "SeriesAdd"):
            return {"accepted": False, "reason": "Ereignis nicht relevant"}
        series = payload.get("series") or {}
        source = series.get("path")
        if not isinstance(source, str):
            raise ValueError("series.path fehlt")
        matches = []
        for lib in self.config.libraries:
            if lib.kind != "tv" or not lib.sonarr_path:
                continue
            suffix = path_suffix(source, lib.sonarr_path)
            if suffix is not None:
                target = lib.path / suffix
                if not contained(target, lib.path):
                    raise ValueError("Pfad außerhalb der Mediathek")
                matches.append((lib, target))
        if len(matches) != 1:
            raise ValueError("Sonarr-Pfad ist nicht eindeutig konfiguriert")
        lib, target = matches[0]
        if not target.is_dir():
            raise ValueError("Serienordner ist noch nicht verfügbar; Polling übernimmt später")
        count = queued = 0
        for folder, dirs, files in os.walk(target, followlinks=False):
            dirs[:] = [name for name in dirs if name.lower() not in EXTRAS and not Path(folder, name).is_symlink()]
            for name in files:
                path = Path(folder, name)
                if self.eligible(path, lib):
                    queued += self.store.enqueue(lib.name, str(path), self.signature(path), self.config.stable_seconds)
                    count += 1
        LOG.info("Sonarr %s: %s, %d Videos geprüft, %d Aufträge aufgenommen", event, lib.name, count, queued)
        return {"accepted": True, "files": count}

    def generate(self, lib, path, ids_only=False, rewrite=False):
        rewrite = rewrite or self.config.replace_existing
        relative = path.relative_to(lib.path)
        if len(relative.parts) < 2:
            raise IdentityError("Ein eigener Film-/Serienordner ist erforderlich")
        home = lib.path / relative.parts[0]
        actions = []
        LOG.info("Online-Metadaten ermitteln: %s", path)
        if lib.kind == "movie":
            videos = [item for item in home.iterdir() if self.eligible(item, lib)]
            if len(videos) != 1 or path.parent != home:
                raise IdentityError("Mehrere Filmdateien/Unterordner benötigen manuelle Klärung")
            output = home / "movie.nfo"
            existing = read_nfo(output) if not rewrite else None
            ids = identities([home.name, path.name], existing)
            identifier = self.tmdb.resolve(ids, "movie")
            data = self.tmdb.details(f"movie/{identifier}")
            actions.append((output, nfo_xml(data, "movie")))
        else:
            output = home / "tvshow.nfo"
            existing = read_nfo(output) if not rewrite else None
            ids = identities([home.name], existing)
            if lib.metadata_source == "tmdb":
                identifier = self.tmdb.resolve(ids, "tv")
                show = self.tmdb.details(f"tv/{identifier}")
            ep_output = path.with_suffix(".nfo")
            ep_existing = read_nfo(ep_output) if not rewrite else None
            season, numbers = episode_numbers(path.stem)
            records = list(ep_existing) if ep_existing is not None and ep_existing.tag == "episodes" else [ep_existing]
            if records != [None] and len(records) != len(numbers):
                raise IdentityError("Episodenanzahl in NFO und Dateiname widerspricht sich")
            if records == [None]:
                records *= len(numbers)
            episode_ids = []
            for episode, record in zip(numbers, records):
                episode_ids.append(identities([], record))
                if record is None:
                    continue
                for key, value in (("season", season), ("episode", episode)):
                    existing_number = record.findtext(key)
                    if existing_number is not None and existing_number != str(value):
                        raise IdentityError("Episodennummer in NFO und Dateiname widerspricht sich")
            contents = []
            if lib.metadata_source == "tvdb":
                if len(numbers) == 1:
                    series_id, single_id = self.sonarr.identify(lib, path)
                    tvdb_ids = [single_id]
                else:
                    series_id, tvdb_ids = self.sonarr.identify_many(lib, path)
                if len(tvdb_ids) != len(numbers):
                    raise IdentityError("Episodenanzahl in Sonarr und Dateiname widerspricht sich")
                if ids.get("tvdb") and ids["tvdb"] != series_id:
                    raise IdentityError("Sonarr-Serie widerspricht vorhandener TVDB-Serien-ID")
                show_base, show = self.tvdb.details("series", series_id)
                show = preserve_external_ids(show, ids)
                for episode, ep_ids, episode_id in zip(numbers, episode_ids, tvdb_ids):
                    if ep_ids.get("tvdb") and ep_ids["tvdb"] != episode_id:
                        raise IdentityError("Sonarr-Episode widerspricht vorhandener TVDB-Episoden-ID")
                    ep_base, data = self.tvdb.details("episodes", episode_id)
                    data = preserve_external_ids(data, ep_ids)
                    if str(show_base.get("id")) != series_id or str(ep_base.get("id")) != episode_id or str(ep_base.get("seriesId")) != series_id:
                        raise IdentityError("TVDB-Episode gehört nicht eindeutig zur ermittelten Serie")
                    LOG.info("Sonarr-Dateizuordnung: TVDB-Serie %s, Episode %s; Texte direkt von TVDB", series_id, episode_id)
                    contents.append(nfo_xml(data, "episode", season, episode, provider="tvdb", provider_id=episode_id))
                actions.append((output, nfo_xml(show, "tv", provider="tvdb", provider_id=series_id)))
            else:
                resolved = set()
                for episode, ep_ids in zip(numbers, episode_ids):
                    data = self.tmdb.episode(identifier, ep_ids, season, episode, lib.season_numbering)
                    if data.get("id") is None or str(data["id"]) in resolved:
                        raise IdentityError("TMDB-Mehrfachepisoden-Zuordnung nicht eindeutig")
                    resolved.add(str(data["id"]))
                    contents.append(nfo_xml(data, "episode", season, episode))
                actions.append((output, nfo_xml(show, "tv")))
            actions.append((ep_output, episode_nfos(contents)))
        expected_digests = {}
        if ids_only or rewrite:
            expected_digests = {output: sha(output.read_bytes()) for output, content in actions if output.exists()}
        if ids_only:
            # Validate all existing targets before writing any of them.
            expected_digests = {output: sha(output.read_bytes()) for output, content in actions if output.exists()}
            actions = [(output, merge_nfo_ids(output, content)) for output, content in actions if output.exists()]
            actions = [(output, content) for output, content in actions if content is not None]
        changed = False
        for output, content in actions:
            if not contained(output, lib.path):
                raise IdentityError("Ausgabepfad außerhalb der Mediathek")
            write_config = dataclasses.replace(self.config, replace_existing=True) if rewrite else self.config
            result = write_nfo(output, content, write_config, self.store, ids_only=ids_only,
                               expected_digest=expected_digests.get(output))
            description = {"written": "geschrieben", "would-write": "würde geschrieben (Vorschau)",
                           "preserved": "vorhandene fremde NFO bleibt erhalten", "unchanged": "unverändert"}[result]
            LOG.info("NFO %s: %s", description, output)
            changed |= result == "written"
        return changed

    def process_one(self):
        job = self.store.claim()
        if job is None:
            return False
        LOG.info("Auftrag gestartet: %s | Phase %s | %s", job["library"], job["phase"], job["path"])
        try:
            path = Path(job["path"])
            lib = self.library(path)
            if lib.name != job["library"] or not self.eligible(path, lib):
                raise IdentityError("Datei nicht mehr gültig")
            signature = self.signature(path)
            if signature != job["signature"]:
                renewed = self.store.reschedule(job, signature, self.config.stable_seconds)
                LOG.info("Auftragssignatur geändert (Datei oder Verarbeitungseinstellungen); %s: %s",
                         "mit aktueller Signatur neu eingereiht" if renewed else "neuerer Auftrag bereits vorhanden", path)
                return True
            if time.time() - path.stat().st_mtime < self.config.stable_seconds:
                self.store.update(job, status="pending", due=time.time() + self.config.stable_seconds)
                LOG.info("Datei noch nicht ruhig; erneute Prüfung in %d s: %s", self.config.stable_seconds, path)
                return True
            if job["phase"] == "metadata":
                if job.get("operation") == "nfo-update":
                    changed = self.generate(lib, path, rewrite=True)
                else:
                    changed = self.generate(lib, path)
                # Also resume refresh after a crash between a successful write and
                # phase persistence, if a generated NFO is still managed by us.
                nfo = path.with_suffix(".nfo") if lib.kind == "tv" else path.parent / "movie.nfo"
                managed = nfo.exists() and self.store.digest(nfo) == sha(nfo.read_bytes())
                if self.config.dry_run or not lib.plex_section or not (changed or managed):
                    self.store.update(job, status="done")
                    reason = "Vorschaumodus" if self.config.dry_run else "kein Plex-Ziel" if not lib.plex_section else "keine verwaltete/geänderte NFO"
                    LOG.info("Auftrag abgeschlossen ohne Plex-Refresh (%s): %s", reason, path)
                    return True
                self.store.update(job, phase="plex")
            LOG.info("Plex-Refresh gestartet: Mediathek %s | %s", lib.name, path)
            self.plex.refresh(lib, path)
            self.store.update(job, status="done", error="")
            LOG.info("Auftrag und Plex-Refresh abgeschlossen: %s", path)
        except IdentityError as exc:
            self.store.update(job, status="failed", error=str(exc))
            LOG.warning("Zuordnung fehlgeschlagen (keine automatische Wiederholung; nach Korrektur erneut anstoßen): %s: %s", job["path"], exc)
        except Exception as exc:
            attempts = job["attempts"] + 1
            # Never persist request objects, URLs containing credentials or tokens.
            error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            delay = min(3600, self.config.retry_seconds * 2 ** min(attempts - 1, 6))
            self.store.update(job, status="failed" if attempts >= 10 else "pending", attempts=attempts,
                              due=time.time() + delay, error=error)
            LOG.warning("Auftrag fehlgeschlagen (Versuch %d/10, %s): %s: %s", attempts,
                        "keine weiteren Versuche" if attempts >= 10 else f"erneut in {delay} s", job["path"], error)
        return True

    def worker(self):
        while not self.stop.is_set():
            if not self.process_one():
                self.stop.wait(1)

    def scanner(self):
        if not self.config.polling_enabled:
            LOG.info("Automatische Ordnerprüfung deaktiviert; Sonarr-Webhooks, wartende Aufträge und manuelle Bestandsprüfung bleiben aktiv")
            return
        while not self.stop.is_set():
            try:
                self.scan()
                self.web.refresh()
            except Exception:
                LOG.exception("Scan fehlgeschlagen")
            self.stop.wait(self.config.poll_seconds)

    def serve(self):
        if len(self.config.webhook_token) < 16:
            raise ValueError("Webhook-Token muss mindestens 16 Zeichen lang sein")
        service = self

        class Handler(BaseHTTPRequestHandler):
            def send_body(self, status, body, content_type):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                if status == 401:
                    self.send_header("WWW-Authenticate", 'Basic realm="Metadachs", charset="UTF-8"')
                self.end_headers()
                self.wfile.write(body)

            def reply(self, status, value):
                body = json.dumps(value, ensure_ascii=False).encode()
                self.send_body(status, body, "application/json; charset=utf-8")

            def authorized(self):
                if self.path != "/webhooks/sonarr" and not service.config.ui_auth_enabled:
                    return True
                auth = self.headers.get("Authorization", "")
                token = ""
                if auth.startswith("Bearer "):
                    token = auth[7:]
                elif auth.startswith("Basic "):
                    try:
                        user, password = base64.b64decode(auth[6:], validate=True).decode().split(":", 1)
                        if user == "metadachs":
                            token = password
                    except (ValueError, UnicodeError):
                        pass
                return hmac.compare_digest(token.encode(), service.config.webhook_token.encode())

            def do_GET(self):
                parsed = urlsplit(self.path)
                route = parsed.path
                if route == "/health":
                    self.reply(200, {"status": "running", "last_scan": service.last_scan,
                                     "scan_errors": len(service.scan_errors), "dry_run": service.config.dry_run})
                    return
                if not self.authorized():
                    self.reply(401, {"error": "Authentifizierung erforderlich"})
                    return
                if route in ("/", "/app.js", "/style.css"):
                    filename, content_type = {"/": ("index.html", "text/html; charset=utf-8"),
                                               "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                                               "/style.css": ("style.css", "text/css; charset=utf-8")}[route]
                    self.send_body(200, (Path(__file__).parent / "static" / filename).read_bytes(), content_type)
                elif route in ("/jobs", "/api/jobs"):
                    self.reply(200, service.store.report())
                elif route == "/api/config":
                    try:
                        self.reply(200, service.web.settings())
                    except (OSError, ValueError):
                        self.reply(500, {"error": "Konfiguration nicht lesbar"})
                elif route == "/api/inventory":
                    query = parse_qs(parsed.query)
                    try:
                        offset = max(0, int(query.get("offset", ["0"])[0]))
                        limit = min(100, max(1, int(query.get("limit", ["50"])[0])))
                        self.reply(200, service.web.inventory(query.get("q", [""])[0], query.get("library", [""])[0], offset, limit))
                    except ValueError:
                        self.reply(400, {"error": "Ungültige Seitenauswahl"})
                elif route == "/api/logs":
                    self.reply(200, service.logs.snapshot())
                elif route == "/api/status":
                    self.reply(200, {"dry_run": service.config.dry_run, "last_scan": service.last_scan,
                                     "ui_auth_enabled": service.config.ui_auth_enabled,
                                     "polling_enabled": service.config.polling_enabled,
                                     "scan_errors": service.scan_errors, "restart_required": service.web.restart_required})
                else:
                    self.reply(404, {"error": "Nicht verfügbar"})

            def do_POST(self):
                if not self.authorized():
                    self.reply(401, {"error": "Authentifizierung erforderlich"})
                    return
                if self.path not in ("/webhooks/sonarr", "/api/config", "/api/inventory/refresh", "/api/jobs/retry", "/api/nfo/update"):
                    self.reply(404, {"error": "Unbekannter Endpunkt"})
                    return
                try:
                    if self.path.startswith("/api/"):
                        # Browser Basic auth is ambient. Require a non-simple custom
                        # header and JSON so cross-site forms cannot mutate settings.
                        if self.headers.get("X-Metadachs-UI") != "1" or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                            self.reply(403, {"error": "UI-Header und JSON erforderlich"})
                            return
                        origin = self.headers.get("Origin")
                        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                            self.reply(403, {"error": "Fremder Ursprung nicht erlaubt"})
                            return
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 1024 * 1024:
                        raise ValueError("Ungültige Payload-Größe")
                    self.connection.settimeout(15)
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError("JSON-Objekt erforderlich")
                    if self.path == "/api/config":
                        result = service.web.save(payload)
                    elif self.path == "/api/inventory/refresh":
                        result = {"started": service.web.refresh()}
                    elif self.path == "/api/nfo/update":
                        result = service.web.update_nfos(payload)
                    elif self.path == "/api/jobs/retry":
                        service.store.retry()
                        result = {"accepted": True}
                    else:
                        result = service.webhook(payload)
                    self.reply(202, result)
                except (ValueError, OSError, TypeError, KeyError, AttributeError):
                    self.reply(400, {"error": "Eingaben ungültig oder nicht speicherbar; Pfade, Variablen, Datentypen und Schreibrechte prüfen. Token mindestens 16 Zeichen."})

            def log_message(self, fmt, *args):
                LOG.debug("HTTP-Anfrage verarbeitet")

        server = ThreadingHTTPServer(("0.0.0.0", self.config.port), Handler)
        LOG.addHandler(self.logs)
        LOG.info("Metadachs gestartet | Modus: %s | %d Mediatheken | Prüfintervall: %d s | Dateiruhe: %d s",
                 "VORSCHAU (keine NFO-Schreibzugriffe/Plex-Refreshs)" if self.config.dry_run else "SCHREIBMODUS",
                 len(self.config.libraries), self.config.poll_seconds, self.config.stable_seconds)
        LOG.info("WebUI und Sonarr-Webhook lauschen auf Port %d; Auftragsverarbeitung aktiv; automatische Ordnerprüfung: %s",
                 self.config.port, "aktiv" if self.config.polling_enabled else "deaktiviert")
        if not self.config.ui_auth_enabled:
            LOG.warning("WebUI-Anmeldung deaktiviert: Jeder mit Netzwerkzugriff kann Einstellungen ändern! Keine öffentliche Freigabe verwenden. Webhook bleibt token-geschützt.")
        if not self.config.tmdb_token and any(lib.metadata_source == "tmdb" for lib in self.config.libraries):
            LOG.warning("TMDB-Token fehlt; TMDB-Mediatheken können noch nicht verarbeitet werden")
        server.daemon_threads = True
        threads = [threading.Thread(target=fn, daemon=True) for fn in (self.worker, self.scanner)]
        for thread in threads:
            thread.start()
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            LOG.info("Metadachs wird beendet; laufende Arbeiten werden abgewartet")
            self.stop.set()
            server.server_close()
            for thread in threads:
                thread.join()
            if self.web.thread:
                self.web.thread.join()
            if self.web.nfo_thread:
                self.web.nfo_thread.join()
            self.store.close()
            LOG.info("Metadachs beendet")
            LOG.removeHandler(self.logs)
            self.logs.close()