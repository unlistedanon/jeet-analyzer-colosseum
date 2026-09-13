from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock
from urllib.parse import urlparse, parse_qs

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from jeet_analyzer_api.sol_payments import PaymentConfig, SolPayments, SYSTEM, GENESIS, b58encode, valid_transfer
from jeet_analyzer_api.app import create_app
from tests.test_beta_api import beta_config, FakeBetaAdapter, INVITE

SIGNATURE = b58encode(bytes([4]) * 64)
RECIPIENT = b58encode(bytes([3]) * 32)
SOURCE = b58encode(bytes([2]) * 32)


@pytest.mark.parametrize("network,cluster", [("mainnet-beta", "mainnet"), ("devnet", "devnet")])
def test_payment_rpc_uses_helius_for_selected_network(monkeypatch, network, cluster):
    monkeypatch.setenv("JEET_SOL_NETWORK", network)
    monkeypatch.setenv("HELIUS_API_KEY", "test-key")
    monkeypatch.delenv("JEET_SOL_RPC_URL", raising=False)
    parsed = urlparse(PaymentConfig.from_env().rpc_url)
    assert parsed.scheme == "https" and parsed.netloc == f"{cluster}.helius-rpc.com"
    assert parsed.path == "/" and parse_qs(parsed.query) == {"api-key": ["test-key"]}
    monkeypatch.setenv("JEET_SOL_RPC_URL", "https://custom.example/rpc")
    assert PaymentConfig.from_env().rpc_url == "https://custom.example/rpc"


def test_payment_rpc_without_key_preserves_devnet(monkeypatch):
    monkeypatch.setenv("JEET_SOL_NETWORK", "devnet")
    monkeypatch.delenv("HELIUS_API_KEY", raising=False)
    monkeypatch.delenv("JEET_SOL_RPC_URL", raising=False)
    assert PaymentConfig.from_env().rpc_url == "https://api.devnet.solana.com"


@pytest.fixture
def payments(tmp_path):
    config = PaymentConfig(enabled=True, recipient=RECIPIENT, price_lamports=10000000, days=30, credits=1200)
    return SolPayments(beta_config(tmp_path), config)


def receipt(invoice):
    return {"blockTime": invoice["created"], "meta": {"err": None, "preBalances": [20000000, 0, 0, 0], "postBalances": [9995000, 10000000, 0, 0]}, "transaction": {"signatures": [SIGNATURE], "message": {
        "accountKeys": [SOURCE, RECIPIENT, SYSTEM, invoice["reference"]],
        "header": {"numRequiredSignatures": 1, "numReadonlyUnsignedAccounts": 2},
        "instructions": [{"programIdIndex": 2, "accounts": [0, 1, 3], "data": b58encode((2).to_bytes(4, "little") + invoice["lamports"].to_bytes(8, "little"))}],
    }}}


def mock_rpc(payments, tx):
    payments.rpc = Mock(side_effect=lambda method, params: GENESIS["devnet"] if method == "getGenesisHash" else tx)


def test_checkout_disabled_and_configuration_fail_closed(tmp_path):
    p = SolPayments(beta_config(tmp_path), PaymentConfig())
    with pytest.raises(HTTPException) as error:
        p.create_invoice("tester")
    assert error.value.status_code == 503
    with pytest.raises(ValueError):
        SolPayments(beta_config(tmp_path), PaymentConfig(enabled=True))


def test_invoice_is_idempotent_and_devnet_never_launches_mainnet_wallet(payments):
    invoice = payments.create_invoice("tester")
    assert payments.create_invoice("tester")["id"] == invoice["id"]
    assert invoice["payment_url"] is None
    assert invoice["amount_sol"] == "0.010000000"
    assert "code_id" not in invoice


def test_valid_payment_persists_and_duplicate_does_not_extend_membership(payments):
    invoice = payments.create_invoice("tester")
    mock_rpc(payments, receipt(invoice))
    result = payments.verify("tester", invoice["id"], SIGNATURE)
    assert result["signature"] == SIGNATURE
    member = payments.membership("tester")
    assert payments.effective_config("tester").max_estimated_credits_per_run == 1200
    assert payments.effective_config("other").max_estimated_credits_per_run == 650
    assert payments.verify("tester", invoice["id"], SIGNATURE) == result
    assert payments.membership("tester") == member
    assert SolPayments(payments.beta, payments.config).membership("tester") == member
    with pytest.raises(HTTPException) as error:
        payments.create_invoice("tester")
    assert error.value.status_code == 409


@pytest.mark.parametrize("mutation", ["failed", "recipient", "amount", "reference", "unbound_reference", "old", "late", "net_outflow", "unsigned", "writable_reference", "signature", "missing_meta"])
def test_rejects_invalid_receipts(payments, mutation):
    invoice = payments.create_invoice("tester")
    tx = receipt(invoice)
    msg = tx["transaction"]["message"]
    if mutation == "failed": tx["meta"]["err"] = {"InstructionError": [0, "InvalidArgument"]}
    if mutation == "recipient": msg["accountKeys"][1] = SOURCE
    if mutation == "amount": msg["instructions"][0]["data"] = b58encode((2).to_bytes(4, "little") + (1).to_bytes(8, "little"))
    if mutation == "reference": msg["accountKeys"][3] = SOURCE
    if mutation == "unbound_reference": msg["instructions"][0]["accounts"] = [0, 1]
    if mutation == "old": tx["blockTime"] = invoice["created"] - 1
    if mutation == "late": tx["blockTime"] = invoice["expires"] + 1
    if mutation == "net_outflow": tx["meta"]["postBalances"][1] = 0
    if mutation == "unsigned": msg["instructions"][0]["accounts"][0] = 2
    if mutation == "writable_reference": msg["header"]["numReadonlyUnsignedAccounts"] = 0
    if mutation == "signature": tx["transaction"]["signatures"] = ["wrong"]
    if mutation == "missing_meta": tx["meta"] = None
    assert not valid_transfer(tx, invoice, SIGNATURE)
    mock_rpc(payments, tx)
    with pytest.raises(HTTPException) as error:
        payments.verify("tester", invoice["id"], SIGNATURE)
    assert error.value.status_code == 422
    assert payments.membership("tester") is None


def test_pending_and_wrong_network_never_grant_access(payments):
    invoice = payments.create_invoice("tester")
    mock_rpc(payments, None)
    with pytest.raises(HTTPException) as error:
        payments.verify("tester", invoice["id"], SIGNATURE)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        payments.verify("tester", invoice["id"], SIGNATURE)
    assert error.value.status_code == 429
    assert payments.membership("tester") is None
    other = payments.create_invoice("other")
    payments.rpc = Mock(return_value=GENESIS["mainnet-beta"])
    with pytest.raises(HTTPException) as error:
        payments.verify("other", other["id"], SIGNATURE)
    assert error.value.status_code == 503


def test_owner_isolation(payments):
    invoice = payments.create_invoice("tester")
    with pytest.raises(HTTPException) as error:
        payments.verify("other", invoice["id"], SIGNATURE)
    assert error.value.status_code == 404


def test_expiry_and_network_isolation(payments):
    invoice = payments.create_invoice("tester")
    mock_rpc(payments, receipt(invoice))
    payments.verify("tester", invoice["id"], SIGNATURE)
    payments.config = replace(payments.config, network="mainnet-beta")
    assert payments.membership("tester") is None
    payments.config = replace(payments.config, network="devnet")
    with payments.connection() as db:
        db.execute("UPDATE sol_memberships SET expires=0")
    assert payments.effective_config("tester").max_estimated_credits_per_run == 650


def test_api_auth_paid_session_and_actual_job_budget(payments):
    app = create_app(beta_config=payments.beta, payment_config=payments.config, engine_adapter=FakeBetaAdapter())
    client = TestClient(app)
    try:
        assert client.post("/api/beta/membership/invoices").status_code == 401
        client.post("/api/beta/session", json={"access_code": INVITE})
        invoice = client.post("/api/beta/membership/invoices").json()
        mock_rpc(app.state.sol_payments, receipt(invoice))
        assert client.post(f'/api/beta/membership/invoices/{invoice["id"]}/verify', json={"signature": SIGNATURE}).status_code == 200
        assert client.get("/api/beta/session").json()["limits"]["estimated_credits_per_run"] == 1200
        app.state.beta_jobs.executor.submit = Mock()
        record, duplicate = app.state.beta_jobs.submit(code_id="tester-01", mint=SOURCE, wallet=RECIPIENT)
        assert record["request"]["max_estimated_provider_credits"] == 1200
        assert record["estimated_credits_reserved"] == 1200
        assert not duplicate
    finally:
        client.close()
        app.state.beta_jobs.shutdown()

def test_detects_payment_by_reference_without_signature(payments):
    invoice = payments.create_invoice('tester')
    def rpc(method, params):
        if method == 'getGenesisHash': return GENESIS['devnet']
        if method == 'getSignaturesForAddress':
            assert params == [invoice['reference'], {'commitment': 'finalized', 'limit': 5}]
            return [{'signature': SIGNATURE, 'err': None}]
        return receipt(invoice)
    payments.rpc = Mock(side_effect=rpc)
    assert payments.verify('tester', invoice['id'])['signature'] == SIGNATURE
    assert payments.membership('tester') is not None


def test_daily_credit_pool_allows_multiple_scans_and_stops_at_cap(payments):
    from jeet_analyzer_api.beta_store import SQLiteBetaStore, BetaLimitError
    payments.config = replace(payments.config, runs_daily=0, daily_credits=1200)
    invoice = payments.create_invoice('tester')
    mock_rpc(payments, receipt(invoice))
    payments.verify('tester', invoice['id'], SIGNATURE)
    config = payments.effective_config('tester')
    assert config.max_runs_per_code_per_day == 0
    store = SQLiteBetaStore(payments.beta.database_path)
    store.initialize()
    for index, reserved in enumerate([1200, 800, 400]):
        row, _ = store.create_or_get(code_id='tester', fingerprint=str(index), request=config.investigation_request(SOURCE, RECIPIENT), config=config)
        assert row['estimated_credits_reserved'] == reserved
        assert row['request']['max_estimated_provider_credits'] == reserved
        if index == 0:
            with pytest.raises(BetaLimitError):
                store.create_or_get(code_id='tester', fingerprint='parallel', request=config.investigation_request(SOURCE, RECIPIENT), config=config)
        store.complete(row['id'], result={'request_telemetry': {'estimated_provider_credits': 400}}, safe_result=None, artifacts={})
    assert payments.daily_usage('tester')['remaining'] == 0
    with pytest.raises(BetaLimitError) as error:
        store.create_or_get(code_id='tester', fingerprint='four', request=config.investigation_request(SOURCE, RECIPIENT), config=config)
    assert error.value.kind == 'BETA_MEMBER_DAILY_CREDITS'
    with payments.connection() as db:
        db.execute("UPDATE beta_investigations SET created_at='2000-01-01T00:00:00Z'")
    assert payments.daily_usage('tester')['remaining'] == 1200


def test_recovery_restores_identity_and_rotates_codes(tmp_path):
    config = beta_config(tmp_path, public_access=True)
    app = create_app(beta_config=config, engine_adapter=FakeBetaAdapter())
    with TestClient(app) as client:
        original = client.get('/api/beta/session').json()['code_id']
        code = client.post('/api/beta/membership/recovery-code').json()['recovery_code']
        new_code = client.post('/api/beta/membership/recovery-code').json()['recovery_code']
        assert code != new_code
        with app.state.sol_payments.connection() as db:
            digest = db.execute('SELECT digest FROM sol_recovery').fetchone()[0]
            assert code not in digest and new_code not in digest
        client.cookies.clear()
        assert client.post('/api/beta/membership/recover', json={'recovery_code': code}).status_code == 401
        restored = client.post('/api/beta/membership/recover', json={'recovery_code': new_code})
        assert restored.status_code == 200
        assert 'HttpOnly' in restored.headers['set-cookie']
        assert client.get('/api/beta/session').json()['code_id'] == original

def test_network_ids_are_full_rpc_genesis_hashes():
    # Captured from official Solana getGenesisHash responses, not shortened CAIP IDs.
    assert GENESIS['mainnet-beta'] == '5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d'
    assert GENESIS['devnet'] == 'EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG'
    from jeet_analyzer_api.sol_payments import b58decode
    assert all(len(b58decode(value)) == 32 for value in GENESIS.values())
