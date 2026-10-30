import dataclasses
import xml.etree.ElementTree as ET
from unittest.mock import Mock

from test_metadachs import Fixture
from metadachs.metadata import IdentityError, nfo_xml, read_nfo
from metadachs.providers import TVDB
from metadachs.service import preserve_external_ids


class ExternalIDTests(Fixture):
    def test_tvdb_translations_include_remote_provider_links(self):
        client = TVDB(self.config, self.store)
        client.get = Mock(side_effect=[{'id': 5, 'remoteIds': [
            {'sourceName': 'TheMovieDB.com', 'id': '123'},
            {'sourceName': 'IMDB', 'id': 'tt1234567'},
            {'sourceName': 'Other', 'id': '999'}]},
            {'name': 'Deutsch', 'overview': 'Inhalt'}])
        _, data = client.details('series', '5')
        self.assertEqual(data['external_ids'], {'tmdb_id': '123', 'imdb_id': 'tt1234567'})
        root = ET.fromstring(nfo_xml(data, 'tv', provider='tvdb', provider_id='5'))
        self.assertEqual(root.find("uniqueid[@type='tmdb']").text, '123')
        self.assertEqual(root.find("uniqueid[@type='imdb']").text, 'tt1234567')
        self.assertEqual(root.find("uniqueid[@type='tvdb']").text, '5')
        self.assertEqual(len(root.findall('uniqueid')), 3)

    def test_invalid_and_conflicting_remote_ids_rejected(self):
        for items in ([{'sourceName': 'TMDB', 'id': 'tt123'}],
                      [{'sourceName': 'IMDB', 'id': '123'}],
                      [{'sourceName': 'TMDB', 'id': '1'}, {'sourceName': 'TheMovieDB', 'id': '2'}]):
            with self.subTest(items=items), self.assertRaises(IdentityError):
                TVDB.external_ids({'remoteIds': items})
        self.assertEqual(TVDB.external_ids({}), {})

    def test_existing_links_preserved_and_conflicts_rejected(self):
        data = preserve_external_ids({'name': 'Deutsch'}, {'tmdb': '123', 'imdb': 'tt456'})
        self.assertEqual(data['external_ids'], {'tmdb_id': '123', 'imdb_id': 'tt456'})
        with self.assertRaises(IdentityError):
            preserve_external_ids({'external_ids': {'tmdb_id': '999'}}, {'tmdb': '123'})

    def test_multi_episode_ids_remain_separate(self):
        path = self.video('Serie {tvdb-5}/Season 01/Serie - S01E01-E02.mkv')
        lib = dataclasses.replace(self.lib, kind='tv', metadata_source='tvdb')
        service = self.service(libraries=(lib,), dry_run=False)
        service.sonarr.identify_many = Mock(return_value=('5', ['10', '20']))
        service.tvdb.details = Mock(side_effect=[
            ({'id': 5}, {'name': 'Serie', 'external_ids': {'tmdb_id': '100'}}),
            ({'id': 10, 'seriesId': 5}, {'name': 'Eins', 'external_ids': {'tmdb_id': '101', 'imdb_id': 'tt101'}}),
            ({'id': 20, 'seriesId': 5}, {'name': 'Zwei', 'external_ids': {'tmdb_id': '102', 'imdb_id': 'tt102'}})])
        service.generate(lib, path)
        root = read_nfo(path.with_suffix('.nfo'))
        self.assertEqual([r.find("uniqueid[@type='tmdb']").text for r in root], ['101', '102'])
        self.assertEqual([r.find("uniqueid[@type='imdb']").text for r in root], ['tt101', 'tt102'])
        show = read_nfo(path.parent.parent / 'tvshow.nfo')
        self.assertEqual(show.find("uniqueid[@type='tmdb']").text, '100')

    def test_tmdb_default_id_not_duplicated(self):
        root = ET.fromstring(nfo_xml({'id': 123, 'external_ids': {'tmdb_id': '123', 'tvdb_id': '5'}}, 'tv'))
        self.assertEqual(len(root.findall("uniqueid[@type='tmdb']")), 1)