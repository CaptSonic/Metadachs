"""Bounded, thread-safe application log for the authenticated WebUI."""

import logging
import re
import threading
from collections import deque
from urllib.parse import quote


class WebLog(logging.Handler):
    def __init__(self, secrets=(), capacity=1000):
        super().__init__(logging.INFO)
        self.entries = deque(maxlen=capacity)
        self.guard = threading.RLock()
        self.sequence = 0
        self.secrets = set()
        self.add_secrets(secrets)
        self.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))

    def add_secrets(self, values):
        with self.guard:
            for value in values:
                if isinstance(value, str) and value:
                    self.secrets.update((value, quote(value, safe='')))

    def redact(self, text):
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, '[REDACTED]')
        text = re.sub(r'(?i)(Bearer\s+|Basic\s+)[^\s,;]+', r'\1[REDACTED]', text)
        text = re.sub(r'(?i)((?:api[_-]?key|token|pin|password|authorization)\s*[=:]\s*)[^\s&,;]+',
                      r'\1[REDACTED]', text)
        text = re.sub(r'(https?://)[^/\s@]+@', r'\1[REDACTED]@', text)
        return text

    def emit(self, record):
        try:
            with self.guard:
                text = self.redact(self.format(record))[:4000]
                self.sequence += 1
                self.entries.append({'id': self.sequence, 'level': record.levelname, 'text': text})
        except Exception:
            # A UI log must never break the worker or recursively log errors.
            pass

    def snapshot(self):
        with self.guard:
            return {'entries': [{**entry, 'text': self.redact(entry['text'])} for entry in self.entries],
                    'capacity': self.entries.maxlen, 'last_id': self.sequence}