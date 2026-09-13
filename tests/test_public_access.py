from dataclasses import replace
import unittest

from jeet_analyzer_api.beta_auth import BETA_COOKIE, read_session
from tests.test_beta_api import BetaApiTests, FakeBetaAdapter, beta_config, API_MINT, API_WALLET, OTHER_WALLET


class PublicAccessTests(BetaApiTests):
    def test_public_visitor_enters_without_code_and_keeps_private_reports(self):
        config = beta_config(self.root, public_access=True, code_hashes={}, max_runs_per_code_per_day=1)
        adapter = FakeBetaAdapter()
        with self.open_client(adapter, config) as client:
            response = client.get('/api/beta/session')
            self.assertTrue(response.json()['authenticated'])
            self.assertTrue(response.json()['public_access'])
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertIn('HttpOnly', response.headers['set-cookie'])
            self.assertIn('SameSite=strict', response.headers['set-cookie'])
            identity = response.json()['code_id']
            token = client.cookies.get(BETA_COOKIE)
            self.assertEqual(client.get('/api/beta/session').json()['code_id'], identity)
            self.assertEqual(adapter.calls, [])
            self.assertEqual(client.get('/api/beta/admin/metrics').status_code, 401)
            admitted = client.post('/api/beta/investigations', json={'mint': API_MINT, 'wallet': API_WALLET})
            self.assertEqual(admitted.status_code, 202)
            identifier = admitted.json()['investigation_id']
            self.wait_for(client, identifier)
            self.assertEqual(client.post('/api/beta/investigations', json={'mint': API_MINT, 'wallet': OTHER_WALLET}).status_code, 429)
            client.cookies.clear()
            self.assertNotEqual(client.get('/api/beta/session').json()['code_id'], identity)
            self.assertEqual(client.get(f'/api/beta/investigations/{identifier}').status_code, 404)
            self.assertEqual(client.get(f'/api/beta/investigations/{identifier}/share').status_code, 404)
            self.assertIsNone(read_session(replace(config, public_access=False), token, kind='beta'))
            self.assertIsNone(read_session(config, token + 'bad', kind='beta'))

    def test_public_access_requires_server_secrets_and_honors_pause(self):
        for changes in ({'session_secret': ''}, {'admin_code_hash': None}):
            with self.open_client(FakeBetaAdapter(), beta_config(self.root, public_access=True, code_hashes={}, **changes)) as client:
                self.assertFalse(client.get('/api/beta/session').json()['authenticated'])
                self.assertEqual(client.get('/ready').status_code, 503)
        with self.open_client(FakeBetaAdapter(), beta_config(self.root, public_access=True, enabled=False)) as client:
            self.assertTrue(client.get('/api/beta/session').json()['authenticated'])
            self.assertEqual(client.post('/api/beta/investigations', json={'mint': API_MINT, 'wallet': API_WALLET}).status_code, 503)
            self.assertEqual(client.post('/api/v1/investigations/cluster-audit', json={'mint': API_MINT, 'wallet': API_WALLET}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
