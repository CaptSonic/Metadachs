import dataclasses
import json
from unittest.mock import Mock

from test_metadachs import Fixture
from metadachs.metadata import IdentityError, merge_nfo_ids, nfo_xml, read_nfo, sha
from metadachs.store import Store


class NFOUpdateTests(Fixture):
    def movie(self, dry_run=False):
        path = self.video()
        service = self.service(dry_run=dry_run)
        service.tmdb.resolve = Mock(return_value=123)
        service.tmdb.details = Mock(return_value={'id': 123, 'title': 'Online-Titel',
            'overview': 'Online-Inhalt', 'external_ids': {'imdb_id': 'tt123'}})
        output = path.parent / 'movie.nfo'
        original = ('<movie><!--Meine Notiz--><title>Eigener Titel</title><plot>Eigener Inhalt</plot>'
                    '<custom><value>Behalten</value></custom><uniqueid type="tmdb" default="true">123</uniqueid></movie>').encode()
        output.write_bytes(original)
        return path, service, output, original

    def test_foreign_nfo_only_missing_ids_added_with_backup(self):
        path, service, output, original = self.movie()
        self.assertTrue(service.generate(self.lib, path, ids_only=True))
        root = read_nfo(output)
        self.assertEqual(root.findtext('title'), 'Eigener Titel')
        self.assertEqual(root.findtext('plot'), 'Eigener Inhalt')
        self.assertEqual(root.findtext('custom/value'), 'Behalten')
        self.assertIn(b'Meine Notiz', output.read_bytes())
        self.assertEqual(root.find("uniqueid[@type='imdb']").text, 'tt123')
        from metadachs.web import inspect_nfo
        self.assertEqual(inspect_nfo(output, self.media)['status'], 'valid')
        json.dumps(inspect_nfo(output, self.media))
        backups = list(output.parent.glob('movie.nfo.*.bak'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertIsNone(service.store.digest(output))
        self.assertFalse(service.generate(self.lib, path, ids_only=True))
        self.assertEqual(len(list(output.parent.glob('movie.nfo.*.bak'))), 1)

    def test_preview_does_not_write_or_backup(self):
        path, service, output, original = self.movie(dry_run=True)
        self.assertFalse(service.generate(self.lib, path, ids_only=True))
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(output.parent.glob('*.bak')), [])

    def test_owned_nfo_remains_managed(self):
        path, service, output, original = self.movie()
        service.store.remember(output, sha(original))
        service.generate(self.lib, path, ids_only=True)
        self.assertEqual(service.store.digest(output), sha(output.read_bytes()))

    def test_concurrent_nfo_change_is_not_overwritten(self):
        from metadachs.metadata import write_nfo
        path, service, output, original = self.movie()
        proposed = merge_nfo_ids(output, nfo_xml({'id': 123, 'external_ids': {'imdb_id': 'tt123'}}, 'movie'))
        output.write_bytes(b'<movie><title>Neue Bearbeitung</title></movie>')
        with self.assertRaisesRegex(IdentityError, 'während der Aktualisierung'):
            write_nfo(output, proposed, service.config, service.store, ids_only=True, expected_digest=sha(original))
        self.assertIn(b'Neue Bearbeitung', output.read_bytes())

    def test_schema_migration_of_existing_job_database(self):
        import sqlite3
        directory = self.root / 'legacy'
        directory.mkdir()
        db = sqlite3.connect(directory / 'metadachs.sqlite3')
        db.execute("""CREATE TABLE jobs (library TEXT,path TEXT,signature TEXT,phase TEXT DEFAULT 'metadata',
            status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,due REAL DEFAULT 0,error TEXT DEFAULT '',
            PRIMARY KEY(library,path))""")
        db.execute("INSERT INTO jobs(library,path,signature) VALUES('Lib','file','sig')")
        db.commit()
        db.close()
        migrated = Store(directory)
        try:
            self.assertEqual(migrated.claim()['operation'], 'metadata')
        finally:
            migrated.close()

    def test_conflict_and_numbering_rejected(self):
        output = self.media / 'episode.nfo'
        output.write_text('<episodedetails><season>1</season><episode>1</episode>'
                          '<uniqueid type="tvdb">10</uniqueid></episodedetails>')
        for episode, identifier in ((2, '10'), (1, '20')):
            with self.subTest(episode=episode, identifier=identifier), self.assertRaises(IdentityError):
                merge_nfo_ids(output, nfo_xml({'name': 'Titel'}, 'episode', 1, episode,
                                            provider='tvdb', provider_id=identifier))

    def test_inventory_request_requeues_done_job_but_not_running(self):
        path, service, output, original = self.movie()
        service.web._build()
        service.store.enqueue(self.lib.name, str(path), service.signature(path))
        job = service.store.claim()
        self.assertEqual(service.web.update_nfos({'path': str(path)})['queued'], 0)
        service.store.update(job, status='done')
        self.assertEqual(service.web.update_nfos({'path': str(path)})['queued'], 1)
        self.assertEqual(service.web.update_nfos({'path': str(path)})['queued'], 0)
        self.assertEqual(service.store.report()[0]['operation'], 'nfo-update')
        service.process_one()
        self.assertEqual(service.store.report()[0]['status'], 'done')
        self.assertEqual(read_nfo(output).findtext('title'), 'Eigener Titel')
        self.assertEqual(service.web.update_nfos({'library': self.lib.name})['queued'], 1)

    def test_invalid_inventory_target_rejected(self):
        service = self.service()
        for payload in ({}, {'path': str(self.root / 'outside.mkv')}, {'library': 'Unbekannt'}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                service.web.update_nfos(payload)

    def test_missing_nfo_is_not_created(self):
        path, service, output, original = self.movie()
        output.unlink()
        service.web._build()
        self.assertEqual(service.web.update_nfos({'path': str(path)})['skipped'], 1)
        service.generate(self.lib, path, ids_only=True)
        self.assertFalse(output.exists())

    def test_restart_keeps_update_operation(self):
        self.store.enqueue_nfo_update('Library', 'file', 'signature')
        self.store.claim()
        recovered = Store(self.config.state)
        try:
            self.assertEqual(recovered.claim()['operation'], 'nfo-update')
        finally:
            recovered.close()

    def test_multi_nfo_keeps_both_plots_and_local_fields(self):
        from metadachs.metadata import episode_nfos
        output = self.media / 'multi.nfo'
        original = [nfo_xml({'name': title, 'overview': title + ' Inhalt'}, 'episode', 8, n,
                            provider='tvdb', provider_id=str(n)) for n, title in ((42, 'Eins'), (43, 'Zwei'))]
        output.write_bytes(episode_nfos(original))
        proposed = [nfo_xml({'name': 'Online', 'external_ids': {'tmdb_id': str(n + 100)}}, 'episode', 8, n,
                           provider='tvdb', provider_id=str(n)) for n in (42, 43)]
        output.write_bytes(merge_nfo_ids(output, episode_nfos(proposed)))
        records = read_nfo(output)
        self.assertEqual([r.findtext('plot') for r in records], ['Eins Inhalt', 'Zwei Inhalt'])
        self.assertEqual([r.find("uniqueid[@type='tmdb']").text for r in records], ['142', '143'])