import dataclasses
import urllib.error
from unittest.mock import Mock, patch

from test_metadachs import Fixture
from metadachs.metadata import TMDB, TMDBNotFound


class TMDBErrorTests(Fixture):
    def client(self):
        return TMDB(dataclasses.replace(self.config, tmdb_token="secret-never-log"), self.store)

    def test_episode_404_has_safe_endpoint_and_numbering_hint(self):
        error = urllib.error.HTTPError("https://example.invalid/?token=secret-never-log", 404, "Not Found", {}, None)
        with patch("metadachs.metadata.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(TMDBNotFound) as caught:
                self.client().get("tv/123/season/2/episode/9")
        message = str(caught.exception)
        self.assertIn("/tv/123/season/2/episode/9", message)
        self.assertIn("Episodenreihenfolge", message)
        self.assertNotIn("secret-never-log", message)
        self.assertNotIn("https://", message)

    def test_show_404_has_identity_hint(self):
        with patch("metadachs.metadata.urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 404, "Not Found", {}, None)):
            with self.assertRaisesRegex(TMDBNotFound, "ID-Zuordnung prüfen"):
                self.client().get("tv/123")

    def test_server_errors_remain_retryable(self):
        with patch("metadachs.metadata.urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 503, "Offline", {}, None)):
            with self.assertRaisesRegex(RuntimeError, "TMDB HTTP 503 bei /tv/123"):
                self.client().get("tv/123")

    def test_404_job_fails_once_without_writing(self):
        path = self.video()
        service = self.service(dry_run=False)
        service.tmdb.resolve = Mock(return_value=123)
        service.tmdb.details = Mock(side_effect=TMDBNotFound("tv/123/season/2/episode/9"))
        service.scan(once=True)
        with self.assertLogs("metadachs", level="INFO") as captured:
            service.process_one()
        self.assertEqual(service.store.report()[0]["status"], "failed")
        self.assertIn("keine automatische Wiederholung", "\n".join(captured.output))
        self.assertFalse(service.process_one())
        self.assertFalse((path.parent / "movie.nfo").exists())
        service.store.retry()
        self.assertIsNotNone(service.store.claim())