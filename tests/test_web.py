import dataclasses
import json
import os
import unittest
from unittest.mock import patch

from test_metadachs import Fixture
from metadachs.config import load
from metadachs.web import inspect_nfo


class InventoryTests(Fixture):
    def test_movie_inventory_fields_search_and_pagination(self):
        path = self.video()
        (path.parent / "movie.nfo").write_text(
            '<movie><title>Äpfel &amp; Birnen</title><plot>Inhalt</plot>'
            '<genre>Drama</genre><runtime>0</runtime><outline />'
            '<uniqueid type="tmdb">123</uniqueid><actor><name>Person</name></actor>'
            '<ratings><rating name="tmdb"><value>7.1</value></rating></ratings></movie>', encoding="utf-8")
        service = self.service()
        service.web._build()
        data = service.web.inventory("äpfel")
        self.assertEqual(data["total"], 1)
        item = data["items"][0]
        self.assertEqual(item["title"], "Äpfel & Birnen")
        self.assertIn("Beschreibung", item["nfo"]["groups"])
        self.assertIn("Besetzung", item["nfo"]["groups"])
        self.assertIn("Bewertungen", item["nfo"]["groups"])
        self.assertNotIn("outline", item["nfo"]["fields"])
        self.assertEqual(item["nfo"]["ids"], {"tmdb": "123"})
        self.assertEqual(service.web.inventory(library="Andere")["total"], 0)
        self.assertEqual(service.web.inventory(offset=1)["items"], [])

    def test_series_episode_missing_and_invalid_nfo(self):
        first = self.video("Serie/Season 01/Serie - S01E01.mkv")
        second = self.video("Serie/Season 01/Serie - S01E02.mkv")
        first.with_suffix(".nfo").write_text('<episodedetails><title>Folge 1</title><season>1</season><episode>1</episode></episodedetails>')
        second.with_suffix(".nfo").write_text('not xml')
        service = self.service(libraries=(dataclasses.replace(self.lib, kind="tv"),))
        service.web._build()
        item = service.web.inventory()["items"][0]
        self.assertEqual(item["nfo"]["status"], "missing")
        self.assertEqual({entry["nfo"]["status"] for entry in item["files"]}, {"valid", "error"})
        self.assertEqual(len(item["files"]), 2)

    def test_inspection_does_not_write_and_rejects_outside(self):
        path = self.media / "movie.nfo"
        content = '<movie><title>&lt;script&gt;alert(1)&lt;/script&gt;</title></movie>'
        path.write_text(content)
        result = inspect_nfo(path, self.media)
        self.assertEqual(result["title"], '<script>alert(1)</script>')
        self.assertEqual(path.read_text(), content)
        self.assertEqual(inspect_nfo(self.root / "outside.nfo", self.media)["status"], "error")

    def test_missing_mount_reported(self):
        service = self.service(libraries=(dataclasses.replace(self.lib, path=self.root / "absent"),))
        service.web._build()
        self.assertEqual(service.web.inventory()["total"], 0)
        self.assertEqual(len(service.web.inventory()["errors"]), 1)


class SettingsTests(Fixture):
    def setUp(self):
        super().setUp()
        self.path = self.root / "config.toml"
        self.path.write_text('[settings]\nstate = ' + json.dumps(str(self.config.state))
                             + '\nwebhook_token = "integration-test-secret"\ntmdb_token = "private-tmdb"\n'
                             + '[[libraries]]\nname="Filme"\nkind="movie"\npath='
                             + json.dumps(str(self.media)) + '\n', encoding="utf-8")
        self.service_instance = self.service()
        self.service_instance.web.config_path = self.path

    def test_secrets_redacted(self):
        data = self.service_instance.web.settings()
        self.assertNotIn("private-tmdb", json.dumps(data))
        self.assertNotIn("integration-test-secret", json.dumps(data))

    def test_save_validated_backup_and_restart_without_runtime_mutation(self):
        original = self.path.read_bytes()
        result = self.service_instance.web.save({"settings": {"poll_seconds": 120, "dry_run": False}, "secrets": {"tmdb_token": ""}})
        self.assertTrue(result["restart_required"])
        self.assertEqual(load(self.path).poll_seconds, 120)
        self.assertEqual(load(self.path).tmdb_token, "private-tmdb")
        self.assertEqual(self.service_instance.config.poll_seconds, 60)
        self.assertEqual(list(self.root.glob("config.toml.*.bak"))[0].read_bytes(), original)
        self.assertEqual(self.service_instance.web.settings()["settings"]["poll_seconds"], 120)

    def test_invalid_save_preserves_original(self):
        original = self.path.read_bytes()
        for payload in ({"settings": {"poll_seconds": 0}}, {"settings": {"dry_run": "false"}},
                        {"libraries": []}, {"secrets": {"webhook_token": "short"}},
                        {"settings": {"unknown": "bad"}}, {"libraries": [{"name": "missing"}]}):
            with self.subTest(payload=payload):
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    self.service_instance.web.save(payload)
                self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list(self.root.glob("config.toml.*.bak")))

    def test_environment_placeholder_preserved(self):
        self.path.write_text(self.path.read_text().replace(json.dumps(str(self.media)), '"${TEST_WEB_MEDIA}"'))
        with patch.dict(os.environ, {"TEST_WEB_MEDIA": str(self.media)}):
            self.service_instance.web.save({"settings": {"language": "de-DE"}})
            self.assertIn('${TEST_WEB_MEDIA}', self.path.read_text())
            self.assertEqual(load(self.path).libraries[0].path, self.media)


if __name__ == "__main__":
    unittest.main()