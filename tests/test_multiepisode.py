import dataclasses
from unittest.mock import Mock

from test_metadachs import Fixture
from metadachs.metadata import IdentityError, episode_numbers, read_nfo, sha
from metadachs.providers import Sonarr
from metadachs.web import inspect_nfo


class MultiEpisodeTests(Fixture):
    def setup_multi(self):
        path = self.video('Serie {tvdb-5}/Season 08/Serie - S08E42-E43 - Zwei Folgen.mkv')
        lib = dataclasses.replace(self.lib, kind='tv', metadata_source='tvdb', sonarr_path='/tv')
        service = self.service(libraries=(lib,), dry_run=False)
        service.sonarr.identify_many = Mock(return_value=('5', ['100', '200']))
        service.tvdb.details = Mock(side_effect=[({'id': 5}, {'name': 'Serie'}),
            ({'id': 100, 'seriesId': 5}, {'name': 'Erste Folge', 'overview': 'Erster Inhalt'}),
            ({'id': 200, 'seriesId': 5}, {'name': 'Zweite Folge', 'overview': 'Zweiter Inhalt'})])
        return path, lib, service

    def test_numbering_ranges_lists_and_specials(self):
        for filename, expected in [('Serie S08E42-E43 - Titel', (8, [42, 43])),
                                   ('Serie S08E01-E03', (8, [1, 2, 3])),
                                   ('Serie S08E01E03', (8, [1, 3])),
                                   ('Serie S00E25', (0, [25]))]:
            with self.subTest(filename=filename):
                self.assertEqual(episode_numbers(filename), expected)
        for filename in ('Serie S01E02-E01', 'Serie S01E01E01', 'Serie S01E01-E9999',
                         'Serie S01E01-S02E01', 'Serie S01E01-02'):
            with self.subTest(filename=filename), self.assertRaises(IdentityError):
                episode_numbers(filename)

    def test_sonarr_returns_all_ids_in_episode_order(self):
        path = self.video('Serie/Serie - S08E42-E43.mkv')
        lib = dataclasses.replace(self.lib, sonarr_path='/tv')
        client = Sonarr(self.config)
        client.get = Mock(side_effect=[[{'id': 1, 'path': '/tv/Serie', 'tvdbId': 5}],
            [{'id': 20, 'path': '/tv/Serie/Serie - S08E42-E43.mkv'}],
            [{'episodeFileId': 20, 'seasonNumber': 1, 'episodeNumber': 4, 'tvdbId': 200},
             {'episodeFileId': 20, 'seasonNumber': 1, 'episodeNumber': 3, 'tvdbId': 100}]])
        self.assertEqual(client.identify_many(lib, path), ('5', ['100', '200']))

    def test_tvdb_multi_nfo_roundtrip_ui_and_managed_reprocessing(self):
        path, lib, service = self.setup_multi()
        service.generate(lib, path)
        output = path.with_suffix('.nfo')
        original = output.read_bytes()
        self.assertEqual(original.count(b'<?xml'), 1)
        root = read_nfo(output)
        self.assertEqual(root.tag, 'episodes')
        self.assertEqual([r.findtext('episode') for r in root], ['42', '43'])
        self.assertEqual([r.findtext('season') for r in root], ['8', '8'])
        self.assertEqual([r.findtext('title') for r in root], ['Erste Folge', 'Zweite Folge'])
        self.assertEqual([r.findtext('plot') for r in root], ['Erster Inhalt', 'Zweiter Inhalt'])
        self.assertEqual([r.find("uniqueid[@type='tvdb']").text for r in root], ['100', '200'])
        view = inspect_nfo(output, self.media)
        self.assertEqual(view['status'], 'valid')
        self.assertEqual(len(view['episodes']), 2)
        self.assertEqual(view['fields']['episode'], ['42', '43'])
        service.tvdb.details.side_effect = [({'id': 5}, {'name': 'Serie'}),
            ({'id': 100, 'seriesId': 5}, {'name': 'Erste Folge', 'overview': 'Erster Inhalt'}),
            ({'id': 200, 'seriesId': 5}, {'name': 'Zweite Folge', 'overview': 'Zweiter Inhalt'})]
        self.assertFalse(service.generate(lib, path))
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(service.store.digest(output), sha(original))
        self.assertEqual(path.read_bytes(), b'test-video')

    def test_sonarr_rejects_duplicate_ids_locations_and_cross_season(self):
        path = self.video('Serie/Serie - S01E01-E02.mkv')
        lib = dataclasses.replace(self.lib, sonarr_path='/tv')
        variants = [
            [(1, 1, 100), (1, 2, 100)],
            [(1, 1, 100), (1, 1, 200)],
            [(1, 1, 100), (2, 1, 200)],
            [(1, 1, 100), (1, 2, 0)],
        ]
        for variant in variants:
            client = Sonarr(self.config)
            client.get = Mock(side_effect=[[{'id': 1, 'path': '/tv/Serie', 'tvdbId': 5}],
                [{'id': 20, 'path': '/tv/Serie/Serie - S01E01-E02.mkv'}],
                [{'episodeFileId': 20, 'seasonNumber': s, 'episodeNumber': e, 'tvdbId': i} for s, e, i in variant]])
            with self.subTest(variant=variant), self.assertRaises(IdentityError):
                client.identify_many(lib, path)

    def test_wrong_series_in_second_episode_prevents_all_writes(self):
        path, lib, service = self.setup_multi()
        service.tvdb.details.side_effect = [({'id': 5}, {'name': 'Serie'}),
            ({'id': 100, 'seriesId': 5}, {'name': 'Erste Folge'}),
            ({'id': 200, 'seriesId': 999}, {'name': 'Fremde Folge'})]
        with self.assertRaisesRegex(IdentityError, 'gehört nicht eindeutig'):
            service.generate(lib, path)
        self.assertFalse(path.with_suffix('.nfo').exists())
        self.assertFalse((self.media / 'Serie {tvdb-5}' / 'tvshow.nfo').exists())

    def test_missing_second_translation_does_not_write_partial_nfo(self):
        path, lib, service = self.setup_multi()
        service.tvdb.details.side_effect = [({'id': 5}, {'name': 'Serie'}),
            ({'id': 100, 'seriesId': 5}, {'name': 'Erste Folge'}), IdentityError('Kein deutscher Titel')]
        with self.assertRaises(IdentityError):
            service.generate(lib, path)
        self.assertFalse(path.with_suffix('.nfo').exists())
        self.assertFalse((self.media / 'Serie {tvdb-5}' / 'tvshow.nfo').exists())

    def test_foreign_multi_nfo_preserved_and_replace_creates_backup(self):
        path, lib, service = self.setup_multi()
        output = path.with_suffix('.nfo')
        original = ('<episodedetails><title>Fremd 1</title><season>8</season><episode>42</episode></episodedetails>'
                    '<episodedetails><title>Fremd 2</title><season>8</season><episode>43</episode></episodedetails>').encode()
        output.write_bytes(original)
        service.generate(lib, path)
        self.assertEqual(output.read_bytes(), original)
        service.config = dataclasses.replace(service.config, replace_existing=True)
        service.tvdb.details.side_effect = [({'id': 5}, {'name': 'Serie'}),
            ({'id': 100, 'seriesId': 5}, {'name': 'Erste Folge'}),
            ({'id': 200, 'seriesId': 5}, {'name': 'Zweite Folge'})]
        service.generate(lib, path)
        self.assertEqual(read_nfo(output)[1].findtext('title'), 'Zweite Folge')
        backups = list(output.parent.glob(output.name + '.*.bak'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_preview_and_plex_refresh_once_per_multi_file(self):
        path, lib, service = self.setup_multi()
        service.config = dataclasses.replace(service.config, dry_run=True)
        service.generate(lib, path)
        self.assertFalse(path.with_suffix('.nfo').exists())
        service.config = dataclasses.replace(service.config, dry_run=False)
        lib = dataclasses.replace(lib, plex_section='1', plex_path='/plex/tv')
        service.config = dataclasses.replace(service.config, libraries=(lib,))
        service.tvdb.details.side_effect = [({'id': 5}, {'name': 'Serie'}),
            ({'id': 100, 'seriesId': 5}, {'name': 'Erste Folge'}),
            ({'id': 200, 'seriesId': 5}, {'name': 'Zweite Folge'})]
        service.plex.refresh = Mock()
        service.store.enqueue(lib.name, str(path), service.signature(path))
        service.process_one()
        self.assertEqual(service.store.report()[0]['status'], 'done')
        service.plex.refresh.assert_called_once_with(lib, path)

    def test_mismatched_episode_count_does_not_fetch_or_write(self):
        path, lib, service = self.setup_multi()
        service.sonarr.identify_many.return_value = ('5', ['100'])
        with self.assertRaisesRegex(IdentityError, 'Episodenanzahl'):
            service.generate(lib, path)
        service.tvdb.details.assert_not_called()
        self.assertFalse(path.with_suffix('.nfo').exists())

    def test_existing_per_episode_id_conflict_preserved(self):
        path, lib, service = self.setup_multi()
        path.with_suffix('.nfo').write_text('<episodedetails><season>8</season><episode>42</episode>'
            '<uniqueid type="tvdb">999</uniqueid></episodedetails>'
            '<episodedetails><season>8</season><episode>43</episode></episodedetails>')
        original = path.with_suffix('.nfo').read_bytes()
        with self.assertRaisesRegex(IdentityError, 'widerspricht'):
            service.generate(lib, path)
        self.assertEqual(path.with_suffix('.nfo').read_bytes(), original)

    def test_tmdb_multi_with_explicit_numbering(self):
        path = self.video('Serie {tmdb-5}/Season 01/Serie - S01E01E02.mkv')
        lib = dataclasses.replace(self.lib, kind='tv', season_numbering=True)
        service = self.service(libraries=(lib,), dry_run=False)
        service.tmdb.resolve = Mock(return_value=5)
        service.tmdb.details = Mock(return_value={'id': 5, 'name': 'Serie'})
        service.tmdb.episode = Mock(side_effect=[{'id': 10, 'name': 'Eins'}, {'id': 20, 'name': 'Zwei'}])
        service.generate(lib, path)
        self.assertEqual([r.findtext('title') for r in read_nfo(path.with_suffix('.nfo'))], ['Eins', 'Zwei'])
        self.assertEqual([call.args[3] for call in service.tmdb.episode.call_args_list], [1, 2])

    def test_multi_xml_security_and_malformed_content(self):
        path = self.media / 'unsafe.nfo'
        for text in ('<!DOCTYPE x><episodedetails/><episodedetails/>',
                     '<episodedetails/><movie/>', '<episodedetails/><episodedetails>'):
            path.write_text(text)
            with self.subTest(text=text), self.assertRaises(IdentityError):
                read_nfo(path)