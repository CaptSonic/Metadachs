import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from test_metadachs import Fixture
from metadachs.config import load


class UIAuthTests(Fixture):
    def test_disabled_ui_remains_protected_for_webhooks(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        config = self.root / 'noauth.toml'
        config.write_text('[settings]\nstate=' + json.dumps(str(self.root / 'http-state'))
                          + f'\nport={port}\nui_auth_enabled=false\npolling_enabled=false\nwebhook_token="integration-test-secret"\n'
                          + '[[libraries]]\nname="Test"\nkind="movie"\npath=' + json.dumps(str(self.media)))
        self.assertFalse(load(config).ui_auth_enabled)
        env = {key: value for key, value in os.environ.items() if not key.startswith('METADACHS_')}
        process = subprocess.Popen([sys.executable, '-m', 'metadachs', '--config', str(config), 'serve'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        try:
            base = f'http://127.0.0.1:{port}'
            for _ in range(100):
                try:
                    with urllib.request.urlopen(base + '/', timeout=1) as response:
                        self.assertIn(b'Metadachs', response.read())
                    break
                except OSError:
                    time.sleep(.05)
            else:
                self.fail('Server startet nicht')
            with urllib.request.urlopen(base + '/api/config', timeout=3) as response:
                data = json.load(response)
                self.assertFalse(data['settings']['ui_auth_enabled'])
                self.assertNotIn('integration-test-secret', json.dumps(data))
            request = urllib.request.Request(base + '/webhooks/sonarr', data=b'{"eventType":"Test"}')
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(error.exception.code, 401)
            error.exception.close()
            request.add_header('Authorization', 'Bearer integration-test-secret')
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertTrue(json.load(response)['test'])
            request = urllib.request.Request(base + '/api/config', data=b'{"settings":{"ui_auth_enabled":true}}',
                                              headers={'Content-Type': 'application/json', 'X-Metadachs-UI': '1'})
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertTrue(json.load(response)['restart_required'])
            self.assertTrue(load(config).ui_auth_enabled)
        finally:
            process.terminate()
            process.wait(timeout=15)