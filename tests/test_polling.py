import dataclasses
import json
from unittest.mock import Mock, patch

from test_metadachs import Fixture
from metadachs.config import load


class PollingTests(Fixture):
    def config_file(self, setting=""):
        path = self.root / "polling.toml"
        path.write_text('[settings]\nstate=' + json.dumps(str(self.config.state)) + '\n' + setting
                        + '\n[[libraries]]\nname="Test"\nkind="movie"\npath='
                        + json.dumps(str(self.media)) + '\n', encoding="utf-8")
        return path

    def test_default_enabled_and_toml_disabled(self):
        self.assertTrue(load(self.config_file()).polling_enabled)
        self.assertFalse(load(self.config_file('polling_enabled=false')).polling_enabled)

    def test_environment_override_and_invalid_values(self):
        path = self.config_file('polling_enabled=true')
        with patch.dict('os.environ', {'METADACHS_POLLING_ENABLED': 'false'}):
            self.assertFalse(load(path).polling_enabled)
        with patch.dict('os.environ', {'METADACHS_POLLING_ENABLED': 'invalid'}):
            with self.assertRaises(ValueError):
                load(path)
        with self.assertRaises(ValueError):
            load(self.config_file('polling_enabled="false"'))

    def test_disabled_scanner_does_not_walk_or_refresh(self):
        service = self.service(polling_enabled=False)
        service.scan = Mock()
        service.web.refresh = Mock()
        with self.assertLogs('metadachs', level='INFO') as logs:
            service.scanner()
        service.scan.assert_not_called()
        service.web.refresh.assert_not_called()
        self.assertIn('Automatische Ordnerprüfung deaktiviert', '\n'.join(logs.output))

    def test_enabled_scanner_still_runs(self):
        service = self.service()
        service.scan = Mock(side_effect=lambda: service.stop.set())
        service.web.refresh = Mock()
        service.scanner()
        service.scan.assert_called_once_with()
        service.web.refresh.assert_called_once_with()

    def test_disabled_polling_allows_manual_inventory_and_cli_scan(self):
        self.video()
        service = self.service(polling_enabled=False)
        service.web._build()
        self.assertEqual(service.web.inventory()['total'], 1)
        self.assertEqual(service.store.report(), [])
        service.scan(once=True)
        self.assertEqual(len(service.store.report()), 1)

    def test_disabled_polling_allows_sonarr_and_existing_jobs(self):
        self.video('Serie/Season 01/Serie - S01E01.mkv')
        lib = dataclasses.replace(self.lib, kind='tv', sonarr_path='/tv')
        service = self.service(polling_enabled=False, libraries=(lib,))
        result = service.webhook({'eventType': 'Download', 'series': {'path': '/tv/Serie'}})
        self.assertTrue(result['accepted'])
        self.assertEqual(len(service.store.report()), 1)
        with patch('metadachs.store.time.time', return_value=10**12):
            self.assertIsNotNone(service.store.claim())

    def test_web_settings_roundtrip(self):
        path = self.config_file('webhook_token="integration-test-secret"')
        service = self.service()
        service.web.config_path = path
        self.assertTrue(service.web.settings()['settings']['polling_enabled'])
        service.web.save({'settings': {'polling_enabled': False}})
        self.assertFalse(load(path).polling_enabled)
        self.assertFalse(service.web.settings()['settings']['polling_enabled'])
        self.assertTrue(service.config.polling_enabled)