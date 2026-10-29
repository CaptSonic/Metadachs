import time
from unittest.mock import Mock, patch

from test_metadachs import Fixture
from metadachs.store import Store


class JobSignatureTests(Fixture):
    def test_restart_recovers_previously_superseded_jobs(self):
        self.store.enqueue('Library', 'file', 'old')
        job = self.store.claim()
        self.store.update(job, status='superseded')
        reader = Store(self.config.state, recover=False)
        self.assertEqual(reader.report()[0]['status'], 'superseded')
        reader.close()
        recovered = Store(self.config.state)
        try:
            renewed = recovered.claim()
            self.assertEqual(renewed['signature'], 'old')
            self.assertEqual(renewed['phase'], 'metadata')
        finally:
            recovered.close()

    def test_legacy_job_requeued_and_processed_without_polling(self):
        path = self.video()
        service = self.service(polling_enabled=False)
        service.generate = Mock(return_value=False)
        service.store.enqueue(self.lib.name, str(path), 'old-release-signature')
        with self.assertLogs('metadachs', level='INFO') as logs:
            self.assertTrue(service.process_one())
        self.assertIn('Verarbeitungseinstellungen', '\n'.join(logs.output))
        self.assertEqual(service.store.report()[0]['status'], 'pending')
        service.generate.assert_not_called()
        self.assertIsNone(service.store.claim())
        with patch('metadachs.store.time.time', return_value=time.time() + 10):
            self.assertTrue(service.process_one())
        service.generate.assert_called_once_with(self.lib, path)
        self.assertEqual(service.store.report()[0]['status'], 'done')
        self.assertEqual(path.read_bytes(), b'test-video')

    def test_reschedule_does_not_overwrite_newer_job(self):
        self.store.enqueue('Library', 'file', 'old')
        old = self.store.claim()
        self.store.enqueue('Library', 'file', 'newest')
        self.assertFalse(self.store.reschedule(old, 'intermediate', 30))
        self.assertEqual(self.store.claim()['signature'], 'newest')

    def test_reschedule_resets_plex_phase_and_failure_state(self):
        self.store.enqueue('Library', 'file', 'old')
        job = self.store.claim()
        self.store.update(job, phase='plex', attempts=3, error='previous failure')
        self.assertTrue(self.store.reschedule(job, 'current', 0))
        renewed = self.store.claim()
        self.assertEqual(renewed['signature'], 'current')
        self.assertEqual(renewed['phase'], 'metadata')
        self.assertEqual(renewed['attempts'], 0)
        self.assertEqual(renewed['error'], '')