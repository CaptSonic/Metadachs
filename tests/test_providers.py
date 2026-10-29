import dataclasses
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

from test_metadachs import Fixture
from metadachs.metadata import IdentityError, nfo_xml
from metadachs.providers import Sonarr, TVDB


class ProviderTests(Fixture):
    def test_sonarr_maps_actual_file_not_season_number(self):
        path = self.video('Wilsberg/Season 09/Wilsberg - S09E01.mkv')
        lib = dataclasses.replace(self.lib, kind='tv', sonarr_path='/tv')
        sonarr = Sonarr(self.config)
        sonarr.get = Mock(side_effect=[
            [{'id': 1, 'path': '/tv/Wilsberg', 'tvdbId': 82504}],
            [{'id': 20, 'path': '/tv/Wilsberg/Season 09/Wilsberg - S09E01.mkv'}],
            [{'episodeFileId': 20, 'tvdbId': 12345, 'title': 'English title'}]])
        self.assertEqual(sonarr.identify(lib, path), ('82504', '12345'))

    def test_sonarr_rejects_multiple_episodes(self):
        path = self.video('Serie/Episode.mkv')
        lib = dataclasses.replace(self.lib, kind='tv', sonarr_path='/tv')
        sonarr = Sonarr(self.config)
        sonarr.get = Mock(side_effect=[[{'id': 1, 'path': '/tv/Serie', 'tvdbId': 5}],
                                      [{'id': 2, 'path': '/tv/Serie/Episode.mkv'}],
                                      [{'episodeFileId': 2, 'tvdbId': 10}, {'episodeFileId': 2, 'tvdbId': 11}]])
        with self.assertRaises(IdentityError):
            sonarr.identify(lib, path)

    def test_tvdb_uses_german_translation_not_base_english(self):
        client = TVDB(dataclasses.replace(self.config, fallback_language=''), self.store)
        client.get = Mock(side_effect=[{'id': 10, 'name': 'English', 'overview': 'English plot'},
                                       {'name': 'Deutscher Titel', 'overview': 'Deutscher Inhalt'}])
        base, data = client.details('episodes', '10')
        self.assertEqual(data['name'], 'Deutscher Titel')
        self.assertEqual(data['overview'], 'Deutscher Inhalt')
        client.get.assert_called_with('episodes/10/translations/deu')

    def test_missing_german_does_not_silently_use_base(self):
        client = TVDB(dataclasses.replace(self.config, fallback_language=''), self.store)
        client.get = Mock(side_effect=[{'name': 'English'}, {}])
        with self.assertRaises(IdentityError):
            client.details('episodes', '10')

    def test_tvdb_login_pin_and_cache(self):
        client = TVDB(dataclasses.replace(self.config, tvdb_api_key='private', tvdb_pin='pin'), self.store)
        with patch('metadachs.providers.request_json', side_effect=[{'data': {'token': 'bearer'}}, {'data': {'id': 10}}]) as request:
            self.assertEqual(client.get('episodes/10/extended'), {'id': 10})
            self.assertEqual(client.get('episodes/10/extended'), {'id': 10})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[2], {'apikey': 'private', 'pin': 'pin'})

    def test_tvdb_nfo_has_no_fake_tmdb_id(self):
        root = ET.fromstring(nfo_xml({'name': 'Deutsch'}, 'episode', 9, 1, provider='tvdb', provider_id='123'))
        self.assertEqual(root.find("uniqueid[@type='tvdb']").text, '123')
        self.assertIsNone(root.find("uniqueid[@type='tmdb']"))

    def test_tvdb_service_without_existing_episode_nfo(self):
        path = self.video('Wilsberg {tvdb-82504}/Season 09/Wilsberg - S09E01.mkv')
        lib = dataclasses.replace(self.lib, kind='tv', metadata_source='tvdb')
        service = self.service(libraries=(lib,), dry_run=False)
        service.sonarr.identify = Mock(return_value=('82504', '12345'))
        service.tvdb.details = Mock(side_effect=[({'id': 82504}, {'name': 'Wilsberg'}),
                                                 ({'id': 12345, 'seriesId': 82504}, {'name': 'Deutsche Folge', 'overview': 'Inhalt'})])
        service.tmdb.episode = Mock()
        service.generate(lib, path)
        root = ET.parse(path.with_suffix('.nfo'))
        self.assertEqual(root.findtext('season'), '9')
        self.assertEqual(root.findtext('title'), 'Deutsche Folge')
        self.assertEqual(root.find("uniqueid[@type='tvdb']").text, '12345')
        service.tmdb.episode.assert_not_called()

    def test_wrong_tvdb_series_prevents_writes(self):
        path = self.video('Serie/Season 01/Serie - S01E01.mkv')
        lib = dataclasses.replace(self.lib, kind='tv', metadata_source='tvdb')
        service = self.service(libraries=(lib,), dry_run=False)
        service.sonarr.identify = Mock(return_value=('1', '2'))
        service.tvdb.details = Mock(side_effect=[({'id': 1}, {'name': 'Serie'}), ({'id': 2, 'seriesId': 99}, {'name': 'Folge'})])
        with self.assertRaises(IdentityError):
            service.generate(lib, path)
        self.assertFalse(path.with_suffix('.nfo').exists())

    def test_settings_save_provider_secrets_and_auth_without_exposing_them(self):
        import json
        from metadachs.config import load
        path = self.root / 'settings.toml'
        path.write_text('[settings]\nstate=' + json.dumps(str(self.config.state))
                        + '\nwebhook_token="integration-test-secret"\n[[libraries]]\nname="Serie"\nkind="tv"\npath='
                        + json.dumps(str(self.media)))
        service = self.service()
        service.web.config_path = path
        service.web.save({'settings': {'sonarr_url': 'http://sonarr:8989', 'ui_auth_enabled': False},
                          'secrets': {'sonarr_api_key': 'sonarr-private', 'tvdb_api_key': 'tvdb-private', 'tvdb_pin': 'private-pin'},
                          'libraries': [{'name': 'Serie', 'kind': 'tv', 'path': str(self.media), 'metadata_source': 'tvdb', 'sonarr_path': '/tv'}]})
        config = load(path)
        self.assertFalse(config.ui_auth_enabled)
        self.assertEqual(config.libraries[0].metadata_source, 'tvdb')
        self.assertEqual(config.tvdb_api_key, 'tvdb-private')
        response = json.dumps(service.web.settings())
        for secret in ('sonarr-private', 'tvdb-private', 'private-pin'):
            self.assertNotIn(secret, response)