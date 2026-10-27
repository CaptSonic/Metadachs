"""Persistent, single-worker queue and HTTP response cache."""

import json
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, directory: Path, recover=True):
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(directory / "metadachs.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
              library TEXT, path TEXT, signature TEXT, phase TEXT DEFAULT 'metadata',
              status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
              due REAL DEFAULT 0, error TEXT DEFAULT '', PRIMARY KEY(library,path));
            CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS managed (path TEXT PRIMARY KEY, digest TEXT);
        """)
        if recover:
            self.db.execute("UPDATE jobs SET status='pending' WHERE status='running'")
        self.db.commit()

    def enqueue(self, library: str, path: str, signature: str, delay: int = 0):
        with self.lock, self.db:
            cursor = self.db.execute("""INSERT INTO jobs(library,path,signature,due) VALUES(?,?,?,?)
                ON CONFLICT(library,path) DO UPDATE SET signature=excluded.signature,
                phase='metadata',status='pending',attempts=0,due=excluded.due,error=''
                WHERE jobs.signature != excluded.signature""", (library, path, signature, time.time() + delay))
            return cursor.rowcount > 0

    def claim(self):
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM jobs WHERE status='pending' AND due<=? ORDER BY due LIMIT 1", (time.time(),)).fetchone()
            if row:
                self.db.execute("UPDATE jobs SET status='running' WHERE library=? AND path=?", (row["library"], row["path"]))
                return dict(row)
        return None

    def update(self, job, **values):
        if not set(values) <= {"status", "phase", "attempts", "due", "error"}:
            raise ValueError("Ungültige Job-Felder")
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET " + ",".join(f"{key}=?" for key in values) + " WHERE library=? AND path=? AND signature=?",
                            (*values.values(), job["library"], job["path"], job["signature"]))

    def get_cache(self, key):
        with self.lock:
            row = self.db.execute("SELECT value FROM cache WHERE key=? AND expires>?", (key, time.time())).fetchone()
            return json.loads(row[0]) if row else None

    def cache(self, key, value, ttl):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (key, json.dumps(value), time.time() + ttl))

    def digest(self, path):
        with self.lock:
            row = self.db.execute("SELECT digest FROM managed WHERE path=?", (str(path),)).fetchone()
            return row[0] if row else None

    def remember(self, path, digest):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO managed VALUES(?,?)", (str(path), digest))

    def report(self):
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT library,path,phase,status,attempts,error FROM jobs ORDER BY library,path")]

    def retry(self):
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET status='pending',attempts=0,due=0,error='' WHERE status='failed'")

    def close(self):
        with self.lock:
            self.db.close()