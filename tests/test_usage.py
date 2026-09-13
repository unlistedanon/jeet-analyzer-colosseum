from contextlib import closing
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from jeet_analyzer_api.app import create_app
from jeet_analyzer_api.usage import UsageStore
from tests.test_beta_api import beta_config, FakeBetaAdapter, ADMIN, API_MINT, API_WALLET


class UsageTests(unittest.TestCase):
    def test_daily_deduplication_ownership_and_server_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            config = beta_config(Path(directory), public_access=True)
            engine = FakeBetaAdapter()
            with TestClient(create_app(beta_config=config, engine_adapter=engine)) as client:
                self.assertEqual(client.post('/api/beta/usage', json={'event': 'visit'}).status_code, 401)
                client.get('/api/beta/session')
                for _ in range(3):
                    self.assertTrue(client.post('/api/beta/usage', json={'event': 'visit'}).json()['stored'])
                self.assertEqual(client.post('/api/beta/usage', json={'event': 'scan_admitted'}).status_code, 422)
                self.assertEqual(client.post('/api/beta/usage', json={'event': 'visit', 'wallet': 'secret'}).status_code, 422)
                self.assertEqual(client.post('/api/beta/usage', json={'event': 'result_viewed', 'investigation_id': 'missing'}).status_code, 404)
                identifier = client.post('/api/beta/investigations', json={'mint': API_MINT, 'wallet': API_WALLET}).json()['investigation_id']
                for _ in range(200):
                    if client.get(f'/api/beta/investigations/{identifier}').json()['status'] == 'complete':
                        break
                    time.sleep(.01)
                self.assertEqual(client.post('/api/beta/usage', json={'event': 'result_viewed', 'investigation_id': identifier}).status_code, 200)
                client.post(f'/api/beta/investigations/{identifier}/feedback', json={'useful': True})
                self.assertEqual(client.get('/api/beta/admin/metrics').status_code, 401)
                client.post('/api/beta/admin/session', json={'access_code': ADMIN})
                metrics = client.get('/api/beta/admin/metrics').json()
                counts = metrics['usage']['counts']
                self.assertEqual(counts['visit'], 1)
                self.assertEqual(counts['scan_admitted'], 1)
                self.assertEqual(counts['result_viewed'], 1)
                self.assertEqual(counts['feedback_submitted'], 1)
                self.assertEqual(metrics['completed_visitors'], 1)
                client.cookies.clear()
                client.get('/api/beta/session')
                self.assertEqual(client.post('/api/beta/usage', json={'event': 'result_viewed', 'investigation_id': identifier}).status_code, 404)
                self.assertEqual(len(engine.calls), 1)
            with closing(sqlite3.connect(config.database_path.with_suffix('.usage.sqlite3'))) as db, db:
                rows = db.execute('SELECT visitor FROM usage_daily').fetchall()
                self.assertTrue(all(len(row[0]) == 64 and 'visitor-' not in row[0] for row in rows))

    def test_retention_and_optional_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'usage.db'
            store = UsageStore(path, 'test')
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("INSERT INTO usage_daily VALUES('2000-01-01','old','visit')")
            self.assertTrue(store.record('visitor-one', 'visit'))
            with closing(sqlite3.connect(path)) as db, db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM usage_daily').fetchone()[0], 1)
            with patch('jeet_analyzer_api.usage.sqlite3.connect', side_effect=sqlite3.OperationalError):
                self.assertFalse(store.record('visitor-one', 'visit'))
