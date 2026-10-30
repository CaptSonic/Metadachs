import dataclasses
import json
import os
import tempfile
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

from metadachs.config import Config, Library, load, path_suffix
from metadachs.metadata import IdentityError, TMDB, identities, nfo_xml, read_nfo, sha, write_nfo
from metadachs.plex import Plex
from metadachs.service import Service
from metadachs.store import Store


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.media = self.root / "media"
        self.media.mkdir()
        self.lib = Library("Filme", "movie", self.media)
        self.config = Config((self.lib,), self.root / "state", stable_seconds=1)
        self.store = Store(self.config.state)

    def tearDown(self):
        self.doCleanups()
        self.store.close()
        self.temp.cleanup()

    def video(self, relative="Film (2024) {tmdb-123}/Film.mkv"):
        path = self.media / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-video")
        old = time.time() - 120
        os.utime(path, (old, old))
        return path

    def service(self, **settings):
        service = Service(dataclasses.replace(self.config, **settings))
        self.addCleanup(service.store.close)
        return service


class ConfigTests(Fixture):
    def config_file(self, extra="", libraries=None):
        path = self.root / "config.toml"
        library = libraries or [{"name": "Film", "kind": "movie", "path": str(self.media)}]
        text = f'[settings]\nstate = {json.dumps(str(self.root / "state"))}\n' + extra
        for item in library:
            text += "\n[[libraries]]\n" + "\n".join(f"{key} = {json.dumps(value)}" for key, value in item.items()) + "\n"
        path.write_text(text, encoding="utf-8")
        return path

    def test_environment_and_secret_override(self):
        path = self.config_file('tmdb_token = "${TEST_TOKEN}"\n')
        with patch.dict(os.environ, {"TEST_TOKEN": "one", "METADACHS_TMDB_TOKEN": "two", "METADACHS_DRY_RUN": "false"}):
            config = load(path)
        self.assertEqual(config.tmdb_token, "two")
        self.assertFalse(config.dry_run)

    def test_missing_variable_rejected(self):
        path = self.config_file('tmdb_token = "${METADACHS_TEST_NONEXISTENT}"\n')
        with self.assertRaisesRegex(ValueError, "Umgebungsvariable fehlt"):
            load(path)

    def test_nested_libraries_rejected(self):
        path = self.config_file(libraries=[{"name": "A", "kind": "movie", "path": str(self.media)},
                                           {"name": "B", "kind": "tv", "path": str(self.media / "sub")}])
        with self.assertRaisesRegex(ValueError, "Überlappende"):
            load(path)

    def test_string_boolean_rejected(self):
        with self.assertRaises(ValueError):
            load(self.config_file('dry_run = "false"\n'))

    def test_mapping_boundaries_windows_and_traversal(self):
        self.assertEqual(path_suffix(r"Z:\TV\Serie\S01", r"z:\tv"), "Serie/S01")
        self.assertIsNone(path_suffix("/tv-other/Serie", "/tv"))
        with self.assertRaises(ValueError):
            path_suffix("/tv/../secret", "/tv")


class StoreTests(Fixture):
    def test_dedup_and_signature_change(self):
        self.store.enqueue("A", "file", "v1")
        job = self.store.claim()
        self.store.update(job, status="done")
        self.store.enqueue("A", "file", "v1")
        self.assertIsNone(self.store.claim())
        self.store.enqueue("A", "file", "v2")
        self.assertEqual(self.store.claim()["signature"], "v2")
        self.store.update(job, status="done")
        self.assertEqual(self.store.report()[0]["status"], "running")

    def test_restart_recovers_running_and_preserves_phase(self):
        self.store.enqueue("A", "file", "v1")
        job = self.store.claim()
        self.store.update(job, phase="plex")
        reader = Store(self.config.state, recover=False)
        self.assertEqual(reader.report()[0]["status"], "running")
        reader.close()
        recovered = Store(self.config.state)
        self.assertEqual(recovered.claim()["phase"], "plex")
        recovered.close()

    def test_cache_and_retry(self):
        self.store.cache("a", {"id": 42}, 60)
        self.assertEqual(self.store.get_cache("a"), {"id": 42})
        self.store.cache("b", {}, -1)
        self.assertIsNone(self.store.get_cache("b"))
        self.store.enqueue("A", "file", "v1")
        job = self.store.claim()
        self.store.update(job, status="failed")
        self.store.retry()
        self.assertIsNotNone(self.store.claim())


class MetadataTests(Fixture):
    def test_conflicting_ids_rejected(self):
        with self.assertRaises(IdentityError):
            identities(["{tmdb-1}", "{tmdb-2}"])

    def test_scraper_url_tolerated_without_modification(self):
        path = self.media / "tvshow.nfo"
        text = '<tvshow><title>Serie</title></tvshow>\nhttps://example.org/series/1\n'
        path.write_text(text, encoding="utf-8")
        self.assertEqual(read_nfo(path).findtext("title"), "Serie")
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_entities_rejected(self):
        path = self.media / "movie.nfo"
        path.write_text('<!DOCTYPE movie [<!ENTITY x "bad">]><movie>&x;</movie>')
        with self.assertRaises(IdentityError):
            read_nfo(path)

    def test_xml_escapes_unicode_and_episode_zero(self):
        content = nfo_xml({"id": 1, "name": "Ä & <B>", "overview": "Österreich", "air_date": "2024-01-02"}, "episode", 0, 1)
        root = ET.fromstring(content)
        self.assertEqual(root.findtext("title"), "Ä & <B>")
        self.assertEqual(root.findtext("season"), "0")
        self.assertEqual(root.findtext("aired"), "2024-01-02")

    def test_dry_run_and_foreign_nfo_protection(self):
        path = self.media / "movie.nfo"
        self.assertEqual(write_nfo(path, b"new", self.config, self.store), "would-write")
        self.assertFalse(path.exists())
        path.write_bytes(b"foreign")
        config = dataclasses.replace(self.config, dry_run=False)
        self.assertEqual(write_nfo(path, b"new", config, self.store), "preserved")
        self.assertEqual(path.read_bytes(), b"foreign")

    def test_managed_update_backup_and_manual_change(self):
        path = self.media / "movie.nfo"
        config = dataclasses.replace(self.config, dry_run=False)
        self.assertEqual(write_nfo(path, b"one", config, self.store), "written")
        self.assertEqual(write_nfo(path, b"two", config, self.store), "written")
        self.assertEqual(list(self.media.glob("*.bak"))[0].read_bytes(), b"one")
        self.assertEqual(self.store.digest(path), sha(b"two"))
        path.write_bytes(b"manual")
        self.assertEqual(write_nfo(path, b"three", config, self.store), "preserved")
        config = dataclasses.replace(config, replace_existing=True)
        self.assertEqual(write_nfo(path, b"three", config, self.store), "written")

    def test_resolver_checks_all_ids(self):
        client = TMDB(self.config, self.store)
        client.get = Mock(return_value={"movie_results": [{"id": 2}]})
        with self.assertRaises(IdentityError):
            client.resolve({"tmdb": "1", "imdb": "tt12"}, "movie")
        self.assertEqual(client.resolve({"imdb": "tt12"}, "movie"), 2)

    def test_external_episode_mapping_and_numbering_opt_in(self):
        client = TMDB(self.config, self.store)
        client.get = Mock(return_value={"tv_episode_results": [{"id": 5, "show_id": 9, "season_number": 2, "episode_number": 3}]})
        client.details = Mock(return_value={"id": 5})
        self.assertEqual(client.episode(9, {"tvdb": "10"}, 1, 1, False), {"id": 5})
        client.details.assert_called_once_with("tv/9/season/2/episode/3")
        with self.assertRaises(IdentityError):
            client.episode(8, {"tvdb": "10"}, 1, 1, False)
        with self.assertRaises(IdentityError):
            client.episode(9, {}, 1, 1, False)

    def test_missing_translation_falls_back(self):
        client = TMDB(self.config, self.store)
        client.get = Mock(side_effect=[{"title": "Deutsch", "overview": ""}, {"title": "English", "overview": "Fallback"}])
        self.assertEqual(client.details("movie/1")["overview"], "Fallback")


class ServiceTests(Fixture):
    def test_scan_logs_counts_and_deduplication(self):
        self.video()
        service = self.service()
        with self.assertLogs("metadachs", level="INFO") as captured:
            service.scan(once=True)
            service.scan(once=True)
        output = "\n".join(captured.output)
        self.assertIn("Ordnerprüfung gestartet", output)
        self.assertIn("1 Videos, 1 neue/geänderte Aufträge", output)
        self.assertIn("1 Videos, 0 neue/geänderte Aufträge", output)
        self.assertIn("nächste Prüfung in 60 s", output)

    def test_preview_job_logs_progress_without_tokens(self):
        self.video()
        service = self.service(tmdb_token="secret-not-to-log")
        service.tmdb.resolve = Mock(return_value=123)
        service.tmdb.details = Mock(return_value={"id": 123, "title": "Film"})
        service.scan(once=True)
        with self.assertLogs("metadachs", level="INFO") as captured:
            service.process_one()
        output = "\n".join(captured.output)
        for expected in ("Auftrag gestartet", "Online-Metadaten ermitteln", "Vorschau", "Auftrag abgeschlossen ohne Plex-Refresh"):
            self.assertIn(expected, output)
        self.assertNotIn("secret-not-to-log", output)

    def test_scan_excludes_extras_and_deduplicates(self):
        self.video()
        self.video("Film (2024) {tmdb-123}/extras/trailer.mkv")
        service = self.service()
        service.scan(once=True)
        service.scan(once=True)
        self.assertEqual(len(service.store.report()), 1)

    def test_scan_requires_stability(self):
        self.video()
        service = self.service()
        service.scan()
        self.assertEqual(service.store.report(), [])
        with patch("metadachs.service.time.time", return_value=time.time() + 2):
            service.scan()
        self.assertEqual(len(service.store.report()), 1)

    def test_missing_mount_does_not_remove_jobs(self):
        path = self.video()
        service = self.service()
        service.scan(once=True)
        lib = dataclasses.replace(self.lib, path=self.root / "missing")
        service.config = dataclasses.replace(service.config, libraries=(lib,))
        service.scan(once=True)
        self.assertEqual(len(service.store.report()), 1)
        self.assertEqual(len(service.scan_errors), 1)
        self.assertTrue(path.exists())

    def test_webhook_test_and_prefix_security(self):
        self.video("Serie {tvdb-99}/Season 01/Serie - S01E01.mkv")
        lib = dataclasses.replace(self.lib, kind="tv", sonarr_path="/tv")
        service = self.service(libraries=(lib,))
        self.assertTrue(service.webhook({"eventType": "Test"})["test"])
        payload = {"eventType": "Download", "series": {"path": "/tv/Serie {tvdb-99}"}}
        self.assertEqual(service.webhook(payload)["files"], 1)
        self.assertEqual(service.webhook(payload)["files"], 1)
        self.assertEqual(len(service.store.report()), 1)
        for invalid in ("/tv-other/Serie", "/tv/../secret"):
            with self.assertRaises(ValueError):
                service.webhook({"eventType": "Rename", "series": {"path": invalid}})

    def test_movie_job_writes_and_refreshes(self):
        path = self.video()
        lib = dataclasses.replace(self.lib, plex_section="1", plex_path="/plex/movies")
        service = self.service(libraries=(lib,), dry_run=False)
        service.tmdb.resolve = Mock(return_value=123)
        service.tmdb.details = Mock(return_value={"id": 123, "title": "Film", "overview": "Plot"})
        service.plex.refresh = Mock()
        service.scan(once=True)
        self.assertTrue(service.process_one())
        self.assertEqual(ET.parse(path.parent / "movie.nfo").findtext("title"), "Film")
        service.plex.refresh.assert_called_once_with(lib, path)
        self.assertEqual(service.store.report()[0]["status"], "done")
        self.assertEqual(path.read_bytes(), b"test-video")

    def test_plex_retry_does_not_regenerate(self):
        self.video()
        lib = dataclasses.replace(self.lib, plex_section="1", plex_path="/plex/movies")
        service = self.service(libraries=(lib,), dry_run=False)
        service.tmdb.resolve = Mock(return_value=123)
        service.tmdb.details = Mock(return_value={"id": 123, "title": "Film"})
        service.plex.refresh = Mock(side_effect=[RuntimeError("Plex nicht erreichbar"), None])
        service.scan(once=True)
        service.process_one()
        report = service.store.report()[0]
        self.assertEqual(report["phase"], "plex")
        self.assertEqual(report["status"], "pending")
        with patch("metadachs.store.time.time", return_value=time.time() + 120):
            service.process_one()
        self.assertEqual(service.tmdb.details.call_count, 1)
        self.assertEqual(service.store.report()[0]["status"], "done")

    def test_preview_does_not_write_or_refresh(self):
        path = self.video()
        service = self.service()
        service.tmdb.resolve = Mock(return_value=123)
        service.tmdb.details = Mock(return_value={"id": 123, "title": "Film"})
        service.plex.refresh = Mock()
        service.scan(once=True)
        service.process_one()
        self.assertFalse((path.parent / "movie.nfo").exists())
        service.plex.refresh.assert_not_called()

    def test_multiepisode_without_unambiguous_tmdb_ids_rejected(self):
        path = self.video("Serie {tmdb-123}/Season 01/Serie - S01E01E02.mkv")
        lib = dataclasses.replace(self.lib, kind="tv", season_numbering=True)
        service = self.service(libraries=(lib,))
        service.tmdb.resolve = Mock(return_value=123)
        service.tmdb.details = Mock(return_value={"id": 123, "name": "Serie"})
        service.tmdb.episode = Mock(return_value={"id": 999, "name": "Nicht eindeutig"})
        with self.assertRaisesRegex(IdentityError, "Mehrfachepisoden-Zuordnung"):
            service.generate(lib, path)

    def test_series_outputs_local_numbering_after_id_mapping(self):
        path = self.video("Serie {tmdb-123}/Season 01/Serie - S01E01.mkv")
        lib = dataclasses.replace(self.lib, kind="tv", season_numbering=True)
        service = self.service(libraries=(lib,), dry_run=False)
        service.tmdb.resolve = Mock(return_value=123)
        service.tmdb.details = Mock(return_value={"id": 123, "name": "Serie"})
        service.tmdb.episode = Mock(return_value={"id": 9, "name": "Folge", "season_number": 2, "episode_number": 3})
        service.generate(lib, path)
        root = ET.parse(path.with_suffix(".nfo"))
        self.assertEqual(root.findtext("season"), "1")
        self.assertEqual(root.findtext("episode"), "1")
        self.assertTrue((self.media / "Serie {tmdb-123}" / "tvshow.nfo").exists())


class PlexTests(Fixture):
    def test_refresh_matching_episode_and_series_only(self):
        path = self.video("Serie/Season 01/Serie - S01E01.mkv")
        lib = dataclasses.replace(self.lib, kind="tv", plex_section="2", plex_path="/plex/tv")
        client = Plex(self.config)
        client.request = Mock(side_effect=[b'<MediaContainer totalSize="2"><Video ratingKey="12" grandparentRatingKey="10"><Media><Part file="/plex/tv/Serie/Season 01/Serie - S01E01.mkv"/></Media></Video><Video ratingKey="999"><Media><Part file="/other.mkv"/></Media></Video></MediaContainer>', b"", b""])
        config = dataclasses.replace(self.config, plex_url="http://plex:32400", plex_token="secret")
        client.config = config
        client.refresh(lib, path)
        endpoints = [call.args[0] for call in client.request.call_args_list]
        self.assertEqual(endpoints, ["/library/sections/2/all", "/library/metadata/10/refresh", "/library/metadata/12/refresh"])

    def test_missing_item_scans_exact_folder_then_retries(self):
        path = self.video()
        lib = dataclasses.replace(self.lib, plex_section="1", plex_path="/plex/movies")
        client = Plex(dataclasses.replace(self.config, plex_url="http://plex:32400", plex_token="secret"))
        client.request = Mock(side_effect=[b'<MediaContainer totalSize="0"/>', b""])
        with self.assertRaisesRegex(RuntimeError, "Scan angefordert"):
            client.refresh(lib, path)
        self.assertEqual(client.request.call_args.kwargs["path"], "/plex/movies/Film (2024) {tmdb-123}")
        self.assertEqual(client.request.call_args.kwargs["method"], "POST")


class HttpIntegrationTests(Fixture):
    def test_real_http_process_health_auth_and_sonarr_test(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        path = self.root / "http.toml"
        path.write_text(
            '[settings]\nstate = ' + json.dumps(str(self.root / "http-state"))
            + f'\nport = {port}\nwebhook_token = "integration-test-secret"\n'
            + '[[libraries]]\nname = "Film"\nkind = "movie"\npath = '
            + json.dumps(str(self.media)) + '\n', encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if not key.startswith("METADACHS_")}
        process = subprocess.Popen([sys.executable, "-m", "metadachs", "--config", str(path), "serve"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(100):
                try:
                    with urllib.request.urlopen(base + "/health", timeout=1) as response:
                        self.assertEqual(json.load(response)["status"], "running")
                    break
                except OSError:
                    if process.poll() is not None:
                        self.fail("HTTP-Dienst vorzeitig beendet")
                    time.sleep(0.05)
            else:
                self.fail("HTTP-Dienst startet nicht")
            request = urllib.request.Request(base + "/webhooks/sonarr", data=b'{"eventType":"Test"}',
                                             headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(error.exception.code, 401)
            error.exception.close()
            request.add_header("Authorization", "Bearer integration-test-secret")
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 202)
                self.assertTrue(json.load(response)["test"])
            basic = urllib.request.Request(base + "/jobs", headers={
                "Authorization": "Basic bWV0YWRhY2hzOmludGVncmF0aW9uLXRlc3Qtc2VjcmV0"})
            with urllib.request.urlopen(basic, timeout=3) as response:
                self.assertEqual(json.load(response), [])
            headers = {"Authorization": "Bearer integration-test-secret"}
            for route, expected in (("/", b"Metadachs"), ("/app.js", b"use strict"), ("/style.css", b":root")):
                with urllib.request.urlopen(urllib.request.Request(base + route, headers=headers), timeout=3) as response:
                    self.assertIn(expected, response.read())
                    self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            with urllib.request.urlopen(urllib.request.Request(base + "/api/config", headers=headers), timeout=3) as response:
                config = json.load(response)
                self.assertNotIn("integration-test-secret", json.dumps(config))
                self.assertTrue(config["writable"])
            with urllib.request.urlopen(urllib.request.Request(base + "/api/inventory", headers=headers), timeout=3) as response:
                self.assertEqual(json.load(response)["total"], 0)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + "/api/config", timeout=3)
            self.assertEqual(error.exception.code, 401)
            error.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + "/api/logs", timeout=3)
            self.assertEqual(error.exception.code, 401)
            error.exception.close()
            with urllib.request.urlopen(urllib.request.Request(base + "/api/logs", headers=headers), timeout=3) as response:
                logs = json.load(response)
                self.assertGreater(len(logs["entries"]), 0)
                self.assertNotIn("integration-test-secret", json.dumps(logs))
            for route in ("/api/inventory/refresh", "/api/jobs/retry"):
                action = urllib.request.Request(base + route, data=b'{}', headers={
                    **headers, "Content-Type": "application/json", "X-Metadachs-UI": "1"})
                with urllib.request.urlopen(action, timeout=3) as response:
                    self.assertEqual(response.status, 202)
            post = urllib.request.Request(base + "/api/config", data=b'{"settings":{"poll_seconds":120}}',
                                          headers={**headers, "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(post, timeout=3)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            post.add_header("X-Metadachs-UI", "1")
            post.add_header("Origin", "http://foreign.invalid")
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(post, timeout=3)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            post.remove_header("Origin")
            with urllib.request.urlopen(post, timeout=3) as response:
                self.assertTrue(json.load(response)["restart_required"])
            self.assertIn('poll_seconds = 120', path.read_text())
            with urllib.request.urlopen(urllib.request.Request(base + "/api/status", headers=headers), timeout=3) as response:
                self.assertTrue(json.load(response)["restart_required"])
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    unittest.main()