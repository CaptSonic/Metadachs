import dataclasses
from unittest.mock import Mock, patch

from test_metadachs import Fixture
from metadachs.metadata import TMDB
from metadachs.plex import Plex


class CacheTests(Fixture):
    def test_tmdb_memory_avoids_repeated_sqlite_reads_and_expires(self):
        client = TMDB(dataclasses.replace(self.config, tmdb_token="test"), self.store)
        with patch.object(self.store, "get_cache", return_value={"id": 123}) as lookup:
            with patch("metadachs.metadata.time.monotonic", return_value=0):
                for _ in range(100):
                    self.assertEqual(client.get("tv/123"), {"id": 123})
            self.assertEqual(lookup.call_count, 1)
            with patch("metadachs.metadata.time.monotonic", return_value=61):
                client.get("tv/123")
            self.assertEqual(lookup.call_count, 2)

    def test_tmdb_memory_is_bounded(self):
        client = TMDB(self.config, self.store)
        for index in range(300):
            client.remember(str(index), {"id": index})
        self.assertEqual(len(client.memory), 256)
        self.assertNotIn("0", client.memory)

    def test_plex_one_inventory_fetch_for_100_refreshes_then_expiry(self):
        path = self.video()
        lib = dataclasses.replace(self.lib, plex_section="1", plex_path="/plex")
        client = Plex(dataclasses.replace(self.config, plex_url="http://plex", plex_token="test"))
        document = b'<MediaContainer totalSize="1"><Video ratingKey="42"><Media><Part file="/plex/Film (2024) {tmdb-123}/Film.mkv"/></Media></Video></MediaContainer>'
        client.request = Mock(side_effect=lambda endpoint, **kwargs: document if endpoint.endswith("/all") else b"")
        with patch("metadachs.plex.time.monotonic", return_value=0):
            for _ in range(100):
                client.refresh(lib, path)
        calls = client.request.call_args_list
        self.assertEqual(sum(call.args[0].endswith("/all") for call in calls), 1)
        self.assertEqual(sum(call.args[0].endswith("/42/refresh") for call in calls), 100)
        with patch("metadachs.plex.time.monotonic", return_value=61):
            client.refresh(lib, path)
        self.assertEqual(sum(call.args[0].endswith("/all") for call in client.request.call_args_list), 2)

    def test_incomplete_plex_index_is_not_cached(self):
        path = self.video()
        lib = dataclasses.replace(self.lib, plex_section="1", plex_path="/plex")
        client = Plex(dataclasses.replace(self.config, plex_url="http://plex", plex_token="test"))
        client.request = Mock(side_effect=RuntimeError("offline"))
        with self.assertRaises(RuntimeError):
            client.refresh(lib, path)
        self.assertEqual(client.indexes, {})