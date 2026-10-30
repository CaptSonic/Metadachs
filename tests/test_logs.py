import json
import logging
import threading
import unittest
from urllib.parse import quote

from metadachs.logs import WebLog


class WebLogTests(unittest.TestCase):
    def emit(self, handler, text, level=logging.INFO):
        handler.handle(logging.LogRecord('metadachs', level, __file__, 1, text, (), None))

    def test_capacity_order_and_limits(self):
        handler = WebLog(capacity=3)
        for n in range(5):
            self.emit(handler, str(n))
        result = handler.snapshot()
        self.assertEqual([entry['id'] for entry in result['entries']], [3, 4, 5])
        self.assertEqual(result['capacity'], 3)
        self.emit(handler, 'x' * 10000)
        self.assertLessEqual(len(handler.snapshot()['entries'][-1]['text']), 4000)
        json.dumps(result)

    def test_secrets_urls_and_authorization_redacted(self):
        secret = 'my/private+secret'
        handler = WebLog([secret])
        self.emit(handler, f'{secret} {quote(secret, safe="")} Bearer hidden-value api_key=other-secret '
                  'https://username:pass@server/path?token=unknown-token&pin=1234')
        text = handler.snapshot()['entries'][0]['text']
        for value in (secret, quote(secret, safe=''), 'hidden-value', 'other-secret',
                      'username:pass', 'unknown-token', '1234'):
            self.assertNotIn(value, text)

    def test_new_secrets_redact_already_buffered_entries(self):
        handler = WebLog()
        self.emit(handler, 'NewSecretValue')
        handler.add_secrets(['NewSecretValue'])
        self.assertNotIn('NewSecretValue', handler.snapshot()['entries'][0]['text'])

    def test_concurrent_logging_and_snapshots(self):
        handler = WebLog(capacity=100)
        threads = [threading.Thread(target=lambda: [self.emit(handler, 'message') for _ in range(100)])
                   for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        result = handler.snapshot()
        self.assertEqual(result['last_id'], 400)
        self.assertEqual(len(result['entries']), 100)

    def test_exception_text_redacted_without_changing_original_record(self):
        handler = WebLog(['private-value'])
        record = logging.LogRecord('metadachs', logging.WARNING, __file__, 1, 'Issue %s', ('private-value',), None)
        handler.handle(record)
        self.assertNotIn('private-value', handler.snapshot()['entries'][0]['text'])
        self.assertEqual(record.getMessage(), 'Issue private-value')