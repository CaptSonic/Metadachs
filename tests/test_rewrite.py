import dataclasses
from unittest.mock import Mock

from test_metadachs import Fixture
from metadachs.metadata import read_nfo
from metadachs.providers import TVDB
from metadachs.providers import Sonarr


class RewriteTests(Fixture):
    def test_wrong_and_malformed_old_episode_nfo_replaced_from_sonarr(self):
        path = self.video('Serie {tvdb-5}/Season 04/Serie - S04E20.mkv')
        lib = dataclasses.replace(self.lib, kind='tv', metadata_source='tvdb')
        service = self.service(libraries=(lib,), dry_run=False)
        service.sonarr.identify = Mock(return_value=('5', '100'))
        output = path.with_suffix('.nfo')
        for original in (b'<episodedetails><uniqueid type="tvdb">999</uniqueid><episode>1</episode></episodedetails>',
                         b'broken old NFO'):
            output.write_bytes(original)
            service.tvdb.details = Mock(side_effect=[({'id': 5}, {'name': 'Serie'}),
                ({'id': 100, 'seriesId': 5}, {'name': 'Neue Folge', 'overview': 'Neuer Inhalt'})])
            service.generate(lib, path, rewrite=True)
            root = read_nfo(output)
            self.assertEqual(root.findtext('title'), 'Neue Folge')
            self.assertEqual(root.findtext('episode'), '20')
            self.assertEqual(root.find("uniqueid[@type='tvdb']").text, '100')
            self.assertTrue(any(p.read_bytes() == original for p in output.parent.glob('*.bak')))

    def test_rewrite_preview_preserves_original(self):
        path = self.video()
        service = self.service()
        output = path.parent / 'movie.nfo'
        output.write_bytes(b'broken original')
        service.tmdb.resolve = Mock(return_value=123)
        service.tmdb.details = Mock(return_value={'id': 123, 'title': 'Neu'})
        service.generate(self.lib, path, rewrite=True)
        self.assertEqual(output.read_bytes(), b'broken original')
        self.assertEqual(list(output.parent.glob('*.bak')), [])

    def test_tvdb_memory_cache_avoids_repeated_sqlite_reads(self):
        client = TVDB(dataclasses.replace(self.config, tvdb_api_key='test'), self.store)
        client.store = Mock()
        client.store.get_cache.return_value = {'id': 5, 'name': 'Serie'}
        first = client.get('series/5/extended')
        first['name'] = 'Mutation'
        self.assertEqual(client.get('series/5/extended')['name'], 'Serie')
        client.store.get_cache.assert_called_once()
        for n in range(600):
            client.remember(str(n), {'id': n})
        self.assertLessEqual(len(client.memory), 512)

    def test_sonarr_indexes_reused_and_rebuilt_after_list_refresh(self):
        path = self.video('Serie/Season 01/Serie - S01E01.mkv')
        lib = dataclasses.replace(self.lib, sonarr_path='/tv')
        client = Sonarr(self.config)
        series = [{'id': 1, 'path': '/tv/Serie', 'tvdbId': 5}]
        files = [{'id': 2, 'path': '/tv/Serie/Season 01/Serie - S01E01.mkv'}]
        episodes = [{'episodeFileId': 2, 'tvdbId': 10}]
        client.get = Mock(side_effect=lambda endpoint: series if endpoint == 'series' else files if endpoint.startswith('episodefile') else episodes)
        self.assertEqual(client.identify(lib, path), ('5', '10'))
        first_index = client.file_indexes[1][1]
        self.assertEqual(client.identify(lib, path), ('5', '10'))
        self.assertIs(client.file_indexes[1][1], first_index)
        files = list(files)
        episodes = [{'episodeFileId': 2, 'tvdbId': 20}]
        self.assertEqual(client.identify(lib, path), ('5', '20'))
        self.assertIsNot(client.file_indexes[1][1], first_index)