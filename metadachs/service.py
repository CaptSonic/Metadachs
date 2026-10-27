"""Polling scanner, durable job execution and authenticated Sonarr HTTP ingress."""

import base64
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
from .metadata import EXTRAS, VIDEO_EXTENSIONS, IdentityError, TMDB, identities, nfo_xml, read_nfo, sha, write_nfo
from .plex import Plex
from .store import Store
from .web import Web

LOG = logging.getLogger("metadachs")


class Service:
    def __init__(self, config, recover=True, config_path=None):
        self.config = config
        self.store = Store(config.state, recover=recover)
        self.tmdb = TMDB(config, self.store)
        self.plex = Plex(config)
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
        policy += f":{lib.season_numbering}:{lib.plex_section}:{lib.plex_path}"
        return sha(f"{stat.st_size}:{stat.st_mtime_ns}:{policy}".encode())

    def eligible(self, path, library):
        return (path.suffix.lower() in VIDEO_EXTENSIONS and path.is_file()
                and not path.is_symlink() and contained(path, library.path)
                and not any(part.lower() in EXTRAS for part in path.relative_to(library.path).parts[:-1]))

    def scan(self, once=False):
        with self.scan_lock:
            errors = []
            current_paths = set()
            for lib in self.config.libraries:
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
                            signature = self.signature(path)
                            now = time.time()
                            previous = self.observed.get(str(path))
                            if previous is None or previous[0] != signature:
                                self.observed[str(path)] = (signature, now)
                            stable_since = self.observed[str(path)][1]
                            if now - path.stat().st_mtime < self.config.stable_seconds:
                                continue
                            if once or now - stable_since >= self.config.stable_seconds:
                                self.store.enqueue(lib.name, str(path), signature)
                        except (OSError, ValueError) as exc:
                            errors.append(f"Datei nicht prüfbar: {path}: {type(exc).__name__}")
            self.observed = {key: value for key, value in self.observed.items() if key in current_paths}
            self.last_scan, self.scan_errors = time.time(), errors
            for error in errors:
                LOG.warning("%s", error)

    def webhook(self, payload):
        event = payload.get("eventType")
        if event == "Test":
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
        count = 0
        for folder, dirs, files in os.walk(target, followlinks=False):
            dirs[:] = [name for name in dirs if name.lower() not in EXTRAS and not Path(folder, name).is_symlink()]
            for name in files:
                path = Path(folder, name)
                if self.eligible(path, lib):
                    self.store.enqueue(lib.name, str(path), self.signature(path), self.config.stable_seconds)
                    count += 1
        return {"accepted": True, "files": count}

    def generate(self, lib, path):
        relative = path.relative_to(lib.path)
        if len(relative.parts) < 2:
            raise IdentityError("Ein eigener Film-/Serienordner ist erforderlich")
        home = lib.path / relative.parts[0]
        actions = []
        if lib.kind == "movie":
            videos = [item for item in home.iterdir() if self.eligible(item, lib)]
            if len(videos) != 1 or path.parent != home:
                raise IdentityError("Mehrere Filmdateien/Unterordner benötigen manuelle Klärung")
            output = home / "movie.nfo"
            existing = read_nfo(output)
            ids = identities([home.name, path.name], existing)
            identifier = self.tmdb.resolve(ids, "movie")
            data = self.tmdb.details(f"movie/{identifier}")
            actions.append((output, nfo_xml(data, "movie")))
        else:
            output = home / "tvshow.nfo"
            existing = read_nfo(output)
            ids = identities([home.name], existing)
            identifier = self.tmdb.resolve(ids, "tv")
            show = self.tmdb.details(f"tv/{identifier}")
            ep_output = path.with_suffix(".nfo")
            ep_existing = read_nfo(ep_output)
            ep_ids = identities([], ep_existing)
            match = re.search(r"(?i)S(\d{1,3})E(\d{1,4})(?!\d)", path.stem)
            if not match or re.search(r"(?i)(?:E\d+|[- ]+\d+|[- ]+E\d+|[- ]+S\d+E\d+)", path.stem[match.end():].split(" - ")[0]):
                raise IdentityError("Nicht unterstützte oder mehrteilige Episodenbenennung")
            season, episode = map(int, match.groups())
            if ep_existing is not None:
                for key, value in (("season", season), ("episode", episode)):
                    existing_number = ep_existing.findtext(key)
                    if existing_number is not None and existing_number != str(value):
                        raise IdentityError("Episodennummer in NFO und Dateiname widerspricht sich")
            data = self.tmdb.episode(identifier, ep_ids, season, episode, lib.season_numbering)
            actions.extend(((output, nfo_xml(show, "tv")), (ep_output, nfo_xml(data, "episode", season, episode))))
        changed = False
        for output, content in actions:
            if not contained(output, lib.path):
                raise IdentityError("Ausgabepfad außerhalb der Mediathek")
            result = write_nfo(output, content, self.config, self.store)
            LOG.info("%s: %s", result, output)
            changed |= result == "written"
        return changed

    def process_one(self):
        job = self.store.claim()
        if job is None:
            return False
        try:
            path = Path(job["path"])
            lib = self.library(path)
            if lib.name != job["library"] or not self.eligible(path, lib):
                raise IdentityError("Datei nicht mehr gültig")
            if self.signature(path) != job["signature"]:
                self.store.update(job, status="superseded")
                return True
            if time.time() - path.stat().st_mtime < self.config.stable_seconds:
                self.store.update(job, status="pending", due=time.time() + self.config.stable_seconds)
                return True
            if job["phase"] == "metadata":
                changed = self.generate(lib, path)
                # Also resume refresh after a crash between a successful write and
                # phase persistence, if a generated NFO is still managed by us.
                nfo = path.with_suffix(".nfo") if lib.kind == "tv" else path.parent / "movie.nfo"
                managed = nfo.exists() and self.store.digest(nfo) == sha(nfo.read_bytes())
                if self.config.dry_run or not lib.plex_section or not (changed or managed):
                    self.store.update(job, status="done")
                    return True
                self.store.update(job, phase="plex")
            self.plex.refresh(lib, path)
            self.store.update(job, status="done", error="")
        except IdentityError as exc:
            self.store.update(job, status="failed", error=str(exc))
            LOG.warning("Zuordnung fehlgeschlagen: %s: %s", job["path"], exc)
        except Exception as exc:
            attempts = job["attempts"] + 1
            # Never persist request objects, URLs containing credentials or tokens.
            error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            self.store.update(job, status="failed" if attempts >= 10 else "pending", attempts=attempts,
                              due=time.time() + min(3600, self.config.retry_seconds * 2 ** min(attempts - 1, 6)), error=error)
            LOG.warning("Auftrag fehlgeschlagen: %s: %s", job["path"], error)
        return True

    def worker(self):
        while not self.stop.is_set():
            if not self.process_one():
                self.stop.wait(1)

    def scanner(self):
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
                elif route == "/api/status":
                    self.reply(200, {"dry_run": service.config.dry_run, "last_scan": service.last_scan,
                                     "scan_errors": service.scan_errors, "restart_required": service.web.restart_required})
                else:
                    self.reply(404, {"error": "Nicht verfügbar"})

            def do_POST(self):
                if not self.authorized():
                    self.reply(401, {"error": "Authentifizierung erforderlich"})
                    return
                if self.path not in ("/webhooks/sonarr", "/api/config", "/api/inventory/refresh", "/api/jobs/retry"):
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
        server.daemon_threads = True
        threads = [threading.Thread(target=fn, daemon=True) for fn in (self.worker, self.scanner)]
        for thread in threads:
            thread.start()
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            self.stop.set()
            server.server_close()
            for thread in threads:
                thread.join()
            if self.web.thread:
                self.web.thread.join()
            self.store.close()