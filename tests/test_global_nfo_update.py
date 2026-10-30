import dataclasses
import threading
from unittest.mock import patch

from test_metadachs import Fixture
from metadachs.config import Library


class GlobalNFOUpdateTests(Fixture):
    def wait(self, service):
        service.web.nfo_thread.join(timeout=5)
        self.assertFalse(service.web.nfo_thread.is_alive())

    def test_all_libraries_without_inventory_or_polling(self):
        first = self.video()
        (first.parent / 'movie.nfo').write_text('<movie><title>Film</title></movie>')
        second_root = self.root / 'other'
        second_root.mkdir()
        second = second_root / 'Serie' / 'Season 01' / 'Serie - S01E01.mkv'
        second.parent.mkdir(parents=True)
        second.write_bytes(b'video')
        second.with_suffix('.nfo').write_text('<episodedetails><season>1</season><episode>1</episode></episodedetails>')
        lib2 = Library('Serien', 'tv', second_root)
        service = self.service(libraries=(self.lib, lib2), polling_enabled=False)
        self.assertEqual(service.web.items, [])
        service.store.enqueue(self.lib.name, str(first), service.signature(first))
        job = service.store.claim()
        service.store.update(job, status='done')
        self.assertTrue(service.web.update_nfos({'all': True})['started'])
        self.wait(service)
        rows = service.store.report()
        self.assertEqual({row['path'] for row in rows}, {str(first), str(second)})
        self.assertTrue(all(row['operation'] == 'nfo-update' for row in rows))
        self.assertTrue(all(row['status'] == 'pending' for row in rows))

    def test_missing_mount_and_missing_nfos_do_not_stop_other_libraries(self):
        path = self.video()
        (path.parent / 'movie.nfo').write_text('<movie/>')
        self.video('Without NFO/file.mkv')
        extra = self.video('Extras/Other/file.mkv')
        (extra.parent / 'movie.nfo').write_text('<movie/>')
        missing = dataclasses.replace(self.lib, name='Offline', path=self.root / 'missing')
        service = self.service(libraries=(missing, self.lib))
        with self.assertLogs('metadachs', level='INFO') as logs:
            service.web.update_nfos({'all': True})
            self.wait(service)
        self.assertEqual([row['path'] for row in service.store.report()], [str(path)])
        self.assertIn('Mount nicht verfügbar', '\n'.join(logs.output))
        self.assertIn('Erfassung abgeschlossen', '\n'.join(logs.output))

    def test_duplicate_global_run_is_not_started(self):
        service = self.service()
        ready, release = threading.Event(), threading.Event()
        def capture():
            ready.set()
            release.wait(timeout=5)
            with service.web.lock:
                service.web.nfo_updating = False
        with patch.object(service.web, '_update_all_nfos', side_effect=capture):
            try:
                self.assertTrue(service.web.update_nfos({'all': True})['started'])
                self.assertTrue(ready.wait(timeout=2))
                self.assertFalse(service.web.update_nfos({'all': True})['started'])
            finally:
                release.set()
                self.wait(service)

    def test_invalid_global_requests_rejected(self):
        service = self.service()
        for payload in ({'all': False}, {'all': 'true'}, {'all': True, 'path': '/outside'}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                service.web.update_nfos(payload)