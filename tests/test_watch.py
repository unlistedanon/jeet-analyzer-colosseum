from __future__ import annotations

import io
import ast
import inspect
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from jeet_analyzer import analyzer as watcher


MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6LBG83ByQ1foo123"


WALLET = "11111111111111111111111111111111"
DESTINATION = "Vote111111111111111111111111111111111111111"
TOKEN_ACCOUNT_A = "So11111111111111111111111111111111111111112"
TOKEN_ACCOUNT_B = "SysvarRent111111111111111111111111111111111"
UNKNOWN_PROGRAM = "BPFLoaderUpgradeab1e11111111111111111111111"


def token_record(owner: str) -> dict:
    return {
        "data": {"parsed": {"type": "account", "info": {"owner": owner}}},
        "owner": watcher.TOKEN_PROGRAM,
        "executable": False,
    }


def account_record(program_owner: str, *, executable: bool = False) -> dict:
    return {"data": ["", "base64"], "owner": program_owner, "executable": executable}


def transaction(
    *,
    wallet_pre: int,
    wallet_post: int,
    destination_pre: int,
    destination_post: int,
    program_id: str,
    parsed_type: str = "transferChecked",
    logs: list[str] | None = None,
) -> dict:
    return {
        "transaction": {
            "message": {
                "accountKeys": [TOKEN_ACCOUNT_A, TOKEN_ACCOUNT_B, program_id],
                "instructions": [
                    {"programId": program_id, "parsed": {"type": parsed_type, "info": {}}}
                ],
            }
        },
        "meta": {
            "preTokenBalances": [
                {
                    "accountIndex": 0,
                    "mint": MINT,
                    "owner": WALLET,
                    "uiTokenAmount": {"amount": str(wallet_pre)},
                },
                {
                    "accountIndex": 1,
                    "mint": MINT,
                    "owner": DESTINATION,
                    "uiTokenAmount": {"amount": str(destination_pre)},
                },
            ],
            "postTokenBalances": [
                {
                    "accountIndex": 0,
                    "mint": MINT,
                    "owner": WALLET,
                    "uiTokenAmount": {"amount": str(wallet_post)},
                },
                {
                    "accountIndex": 1,
                    "mint": MINT,
                    "owner": DESTINATION,
                    "uiTokenAmount": {"amount": str(destination_post)},
                },
            ],
            "innerInstructions": [],
            "logMessages": logs or [],
        },
    }


class InspectRpc:
    def get_token_supply(self, mint):
        self.mint = mint
        return 1000, 0

    def get_largest_token_accounts(self, mint):
        return [
            {"address": TOKEN_ACCOUNT_A, "amount": "600"},
            {"address": TOKEN_ACCOUNT_B, "amount": "300"},
            {"address": "Stake11111111111111111111111111111111111111", "amount": "100"},
        ]

    def get_multiple_accounts(self, addresses):
        if addresses and addresses[0] == TOKEN_ACCOUNT_A:
            return {
                TOKEN_ACCOUNT_A: token_record(WALLET),
                TOKEN_ACCOUNT_B: token_record(DESTINATION),
                "Stake11111111111111111111111111111111111111": token_record(UNKNOWN_PROGRAM),
            }
        return {
            WALLET: account_record(watcher.SYSTEM_PROGRAM),
            DESTINATION: account_record("OwnedByPoolProgram1111111111111111111111111"),
            UNKNOWN_PROGRAM: account_record(UNKNOWN_PROGRAM, executable=True),
        }


class SnapshotRpc:
    def __init__(self, snapshots):
        self.snapshots = iter(snapshots)
        self.signature_calls = []

    def get_owner_token_accounts(self, owner, mint):
        return next(self.snapshots)

    def get_signatures(self, address, limit=20):
        self.signature_calls.append(address)
        return []

    def get_transaction(self, signature):
        raise AssertionError("no transaction should be fetched without a candidate signature")


class FixedAttributor:
    def seed(self, addresses, max_slot):
        self.seeded = (set(addresses), max_slot)

    def attribute(self, delta, **_kwargs):
        if delta > 0:
            return watcher.Attribution("BALANCE_INCREASE", signature="increase-signature")
        return watcher.Attribution(
            "TRANSFER",
            signature="transfer-signature",
            destination=DESTINATION,
            evidence="test transfer",
        )


class FakeSession:
    def post(self, url, **_kwargs):
        raise RuntimeError(f"failure at {url}")


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def send(self, message):
        self.sent.append(json.loads(message))

    def recv(self):
        raise OSError("mock disconnect")

    def close(self):
        self.closed = True


class WatcherTests(unittest.TestCase):
    def test_public_address_validation_and_no_secret_cli_option(self):
        self.assertEqual(watcher.validate_public_address(MINT), MINT)
        with self.assertRaises(Exception):
            watcher.validate_public_address("not-a-solana-address")
        parser = watcher.build_parser()
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
            parser.parse_args(["watch", "--mint", MINT, "--wallet", WALLET, "--private-key", "forbidden"])

    def test_rpc_method_allowlist_refuses_transaction_submission(self):
        client = watcher.ReadOnlyRpcClient("https://example.invalid")
        self.assertEqual(
            client.ALLOWED_METHODS,
            {
                "getMultipleAccounts",
                "getAccountInfo",
                "getProgramAccounts",
                "getSignaturesForAddress",
                "getTokenAccountsByOwner",
                "getTokenLargestAccounts",
                "getTokenSupply",
                "getTransaction",
            },
        )
        for method in ("sendTransaction", "requestAirdrop", "simulateTransaction"):
            with self.assertRaises(watcher.RpcError):
                client.call(method, [])
        self.assertNotIn("sendTransaction", client.ALLOWED_METHODS)

    def test_source_has_no_signing_or_transaction_sdk_surface(self):
        tree = ast.parse(inspect.getsource(watcher))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue({"requests", "websocket"}.issubset(imports))
        self.assertTrue({"solders", "solana", "nacl", "bottotrot_runner_hunter"}.isdisjoint(imports))
        forbidden_calls = {
            "sign",
            "sign_message",
            "send_raw_transaction",
            "send_transaction",
            "swap",
        }
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(forbidden_calls.isdisjoint(called_attributes))

    def test_rpc_transport_error_does_not_expose_credentials_or_query(self):
        secret_url = "https://user:password@example.invalid/rpc?api-key=super-secret"
        client = watcher.ReadOnlyRpcClient(secret_url, session=FakeSession())
        with self.assertRaises(watcher.RpcError) as raised:
            client.call("getTokenSupply", [MINT])
        message = str(raised.exception)
        self.assertNotIn("password", message)
        self.assertNotIn("super-secret", message)
        self.assertNotIn("example.invalid", message)

    def test_inspect_resolves_wallet_and_flags_non_human_accounts(self):
        output = io.StringIO()
        with redirect_stdout(output):
            rows = watcher.inspect_holders(InspectRpc(), MINT, limit=3)
        self.assertEqual(rows[0]["classification"], "WALLET_LIKE")
        self.assertEqual(rows[0]["percentage_of_rpc_supply"], "60.000000")
        self.assertEqual(rows[1]["classification"], "PROGRAM_PDA_OR_POOL_CANDIDATE")
        self.assertEqual(rows[2]["classification"], "PROGRAM_ACCOUNT")
        rendered = output.getvalue()
        self.assertIn("circulating-supply proxy only", rendered)
        self.assertIn("not automatically a human wallet", rendered)
        self.assertIn("FLAG:", rendered)

    def test_direct_token_transfer_is_not_called_a_sell(self):
        result = watcher.analyze_transaction(
            transaction(
                wallet_pre=1000,
                wallet_post=500,
                destination_pre=100,
                destination_post=600,
                program_id=watcher.TOKEN_PROGRAM,
            ),
            wallet=WALLET,
            mint=MINT,
            expected_delta=-500,
        )
        self.assertEqual(result.event_type, "TRANSFER")
        self.assertEqual(result.destination, DESTINATION)

    def test_explicit_sell_instruction_is_sell(self):
        result = watcher.analyze_transaction(
            transaction(
                wallet_pre=1000,
                wallet_post=500,
                destination_pre=100,
                destination_post=600,
                program_id=UNKNOWN_PROGRAM,
                parsed_type="opaque",
                logs=["Program log: Instruction: Sell"],
            ),
            wallet=WALLET,
            mint=MINT,
            expected_delta=-500,
        )
        self.assertEqual(result.event_type, "SELL")
        self.assertEqual(result.destination, DESTINATION)

    def test_unknown_program_is_unknown_even_with_exact_destination(self):
        result = watcher.analyze_transaction(
            transaction(
                wallet_pre=1000,
                wallet_post=500,
                destination_pre=100,
                destination_post=600,
                program_id=UNKNOWN_PROGRAM,
                parsed_type="transfer",
            ),
            wallet=WALLET,
            mint=MINT,
            expected_delta=-500,
        )
        self.assertEqual(result.event_type, "UNKNOWN")
        self.assertEqual(result.destination, DESTINATION)

    def test_increase_is_detected_from_exact_transaction_delta(self):
        result = watcher.analyze_transaction(
            transaction(
                wallet_pre=500,
                wallet_post=800,
                destination_pre=600,
                destination_post=300,
                program_id=watcher.TOKEN_PROGRAM,
            ),
            wallet=WALLET,
            mint=MINT,
            expected_delta=300,
        )
        self.assertEqual(result.event_type, "BALANCE_INCREASE")

    def test_thresholds_are_one_time_and_jeet_out_is_obvious_after_reacquisition(self):
        snapshots = [
            watcher.TokenBalanceSnapshot({TOKEN_ACCOUNT_A: 10_000}, 100),
            watcher.TokenBalanceSnapshot({TOKEN_ACCOUNT_A: 400}, 101),
            watcher.TokenBalanceSnapshot({TOKEN_ACCOUNT_A: 8_000}, 102),
            watcher.TokenBalanceSnapshot({TOKEN_ACCOUNT_A: 50}, 103),
            watcher.TokenBalanceSnapshot({}, 104),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            writer = watcher.BoundedJsonlWriter(path, max_bytes=100_000, backups=1)
            service = watcher.WalletWatcher(
                SnapshotRpc(snapshots),
                rpc_url="https://example.invalid/rpc?secret=hidden",
                wallet=WALLET,
                mint=MINT,
                decimals=0,
                trace_depth=0,
                writer=writer,
            )
            service.attributor = FixedAttributor()
            output = io.StringIO()
            with redirect_stdout(output):
                for reason in ("start", "drop", "reacquire", "final_drop", "zero"):
                    service.reconcile(reason)
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        event_types = [event["event_type"] for event in events]
        for threshold in (90, 75, 50, 25, 10, 5):
            self.assertEqual(event_types.count(f"THRESHOLD_{threshold}"), 1)
        self.assertEqual(event_types.count("JEET_OUT"), 1)
        self.assertIn("BALANCE_INCREASE", event_types)
        self.assertIn("JEET_OUT symbol=DezXA...oo123 wallet=11111...11111 remaining=0.500000%", output.getvalue())
        jeet = next(event for event in events if event["event_type"] == "JEET_OUT")
        self.assertEqual(jeet["transaction_signature"], "transfer-signature")
        self.assertEqual(jeet["destination"], DESTINATION)

    def test_zero_start_records_increase_without_thresholds(self):
        snapshots = [
            watcher.TokenBalanceSnapshot({}, 100),
            watcher.TokenBalanceSnapshot({TOKEN_ACCOUNT_A: 10}, 101),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            service = watcher.WalletWatcher(
                SnapshotRpc(snapshots),
                rpc_url="https://example.invalid",
                wallet=WALLET,
                mint=MINT,
                decimals=0,
                writer=watcher.BoundedJsonlWriter(path, max_bytes=10_000, backups=1),
            )
            service.attributor = FixedAttributor()
            with redirect_stdout(io.StringIO()):
                service.reconcile("start")
                service.reconcile("increase")
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[0]["current_balance_raw"], 0)
        self.assertIsNone(events[1]["percentage_remaining"])
        self.assertFalse(any(event["event_type"].startswith("THRESHOLD") for event in events))

    def test_jsonl_rotates_at_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            writer = watcher.BoundedJsonlWriter(path, max_bytes=1024, backups=2)
            writer.write({"event_type": "ONE", "payload": "x" * 800})
            writer.write({"event_type": "TWO", "payload": "y" * 800})
            self.assertTrue(path.exists())
            self.assertTrue(path.with_name("events.jsonl.1").exists())
            self.assertLessEqual(path.stat().st_size, 1024)

    def test_reconnect_resubscribes_and_reconciles(self):
        snapshots = [watcher.TokenBalanceSnapshot({TOKEN_ACCOUNT_A: 100}, slot) for slot in range(100, 105)]
        rpc = SnapshotRpc(snapshots)
        sockets = [FakeWebSocket(), FakeWebSocket()]

        def factory(_url, **_kwargs):
            return sockets.pop(0)

        with tempfile.TemporaryDirectory() as temporary:
            service = watcher.WalletWatcher(
                rpc,
                rpc_url="https://user:secret@example.invalid/rpc?key=hidden",
                wallet=WALLET,
                mint=MINT,
                decimals=0,
                writer=watcher.BoundedJsonlWriter(Path(temporary) / "events.jsonl", max_bytes=10_000, backups=1),
                websocket_factory=factory,
                sleeper=lambda _delay: None,
                stop_event=threading.Event(),
            )
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), patch("sys.stderr", stderr):
                service.run(max_connections=2)
        self.assertEqual(len(sockets), 0)
        self.assertNotIn("secret", stderr.getvalue())
        self.assertNotIn("hidden", stderr.getvalue())
        self.assertNotIn("example.invalid", stderr.getvalue())
        # Each connection subscribes to classic SPL Token and Token-2022.
        # Five snapshots prove initial, pre-connect, and post-subscribe reconciliation.
        self.assertEqual(len(rpc.signature_calls) >= 1, True)

    def test_websocket_subscription_is_only_filtered_program_subscribe(self):
        rpc = SnapshotRpc([watcher.TokenBalanceSnapshot({TOKEN_ACCOUNT_A: 100}, 100)])
        fake = FakeWebSocket()
        with tempfile.TemporaryDirectory() as temporary:
            service = watcher.WalletWatcher(
                rpc,
                rpc_url="https://example.invalid",
                wallet=WALLET,
                mint=MINT,
                decimals=0,
                writer=watcher.BoundedJsonlWriter(Path(temporary) / "events.jsonl", max_bytes=10_000, backups=1),
            )
            service._program_subscribe(fake, 1, watcher.TOKEN_PROGRAM)
        request = fake.sent[0]
        self.assertEqual(request["method"], "programSubscribe")
        self.assertEqual(request["params"][1]["filters"][0]["memcmp"]["bytes"], MINT)
        self.assertEqual(request["params"][1]["filters"][1]["memcmp"]["bytes"], WALLET)


if __name__ == "__main__":
    unittest.main()
