from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jeet_analyzer import history
from jeet_analyzer.analyzer import TokenBalanceSnapshot
from jeet_analyzer.tx_classifier import (
    ASSOCIATED_TOKEN_PROGRAM,
    COMPUTE_BUDGET_PROGRAM,
    KNOWN_MARKET_PROGRAMS,
    SYSTEM_PROGRAM,
    TOKEN_2022_PROGRAM,
    TOKEN_PROGRAM,
)


TARGET_MINT = "TargetMint11111111111111111111111111111111111"
OTHER_MINT = "OtherMint11111111111111111111111111111111112"
ROOT = "RootWallet1111111111111111111111111111111111"
WALLET_B = "RelatedWalletB1111111111111111111111111111111"
WALLET_C = "RelatedWalletC1111111111111111111111111111111"
WALLET_D = "RelatedWalletD1111111111111111111111111111111"
FUNDER = "FundingWallet11111111111111111111111111111111"
POOL = "PoolAuthority1111111111111111111111111111111"
UNKNOWN_PROGRAM = "UnknownProgram11111111111111111111111111111"
ROUTER = next(iter(KNOWN_MARKET_PROGRAMS))


def account_record(*, owner: str = SYSTEM_PROGRAM, executable: bool = False) -> dict[str, Any]:
    return {"owner": owner, "executable": executable, "data": ["", "base64"]}


def _balance(index: int, mint: str, owner: str, amount: int, decimals: int = 6) -> dict[str, Any]:
    return {
        "accountIndex": index,
        "mint": mint,
        "owner": owner,
        "uiTokenAmount": {"amount": str(amount), "decimals": decimals},
    }


def token_transfer_tx(
    signature: str,
    *,
    source_owner: str = ROOT,
    destination_owner: str = WALLET_B,
    mint: str = OTHER_MINT,
    amount: int = 100,
    block_time: int = 1_800_000_000,
    token_program: str = TOKEN_2022_PROGRAM,
    inner: bool = True,
    outer_program: str | None = None,
    destination_account: str | None = None,
    create_ata: bool = False,
    rent_lamports: int = 2_039_280,
    source_pre: int | None = None,
    destination_pre: int = 0,
) -> dict[str, Any]:
    source_account = f"SourceToken{signature}"
    destination_account = destination_account or f"DestinationToken{signature}"
    source_pre = amount if source_pre is None else source_pre
    keys = [
        {"pubkey": source_owner, "signer": True},
        {"pubkey": source_account, "signer": False},
        {"pubkey": destination_account, "signer": False},
        {"pubkey": destination_owner, "signer": False},
        {"pubkey": token_program, "signer": False},
    ]
    instructions: list[dict[str, Any]] = []
    if outer_program:
        keys.append({"pubkey": outer_program, "signer": False})
        instructions.append(
            {
                "programId": outer_program,
                "accounts": [source_account, "WrongPositionalDestination111111111111111111"],
                "data": "opaque",
            }
        )
    if create_ata:
        keys.append({"pubkey": ASSOCIATED_TOKEN_PROGRAM, "signer": False})
        instructions.append(
            {
                "programId": ASSOCIATED_TOKEN_PROGRAM,
                "parsed": {
                    "type": "createIdempotent",
                    "info": {
                        "payer": source_owner,
                        "wallet": destination_owner,
                        "account": destination_account,
                        "mint": mint,
                    },
                },
            }
        )
        instructions.append(
            {
                "programId": SYSTEM_PROGRAM,
                "parsed": {
                    "type": "transfer",
                    "info": {"source": source_owner, "destination": destination_account, "lamports": rent_lamports},
                },
            }
        )
    parsed_transfer = {
        "programId": token_program,
        "parsed": {
            "type": "transferChecked",
            "info": {
                "source": source_account,
                "destination": destination_account,
                "authority": source_owner,
                "mint": mint,
                "tokenAmount": {"amount": str(amount), "decimals": 6},
            },
        },
    }
    inner_rows = []
    if inner:
        inner_rows = [{"index": max(len(instructions) - 1, 0), "instructions": [parsed_transfer]}]
    else:
        instructions.append(parsed_transfer)
    fee = 5_000
    pre_lamports = [1_000_000_000, 0, 0, 1_000_000, 0] + [0] * (len(keys) - 5)
    post_lamports = list(pre_lamports)
    post_lamports[0] -= fee + (rent_lamports if create_ata else 0)
    if create_ata:
        post_lamports[2] += rent_lamports
    return {
        "blockTime": block_time,
        "transaction": {
            "signatures": [signature],
            "message": {"accountKeys": keys, "instructions": instructions},
        },
        "meta": {
            "err": None,
            "fee": fee,
            "preBalances": pre_lamports,
            "postBalances": post_lamports,
            "preTokenBalances": [
                _balance(1, mint, source_owner, source_pre),
                _balance(2, mint, destination_owner, destination_pre),
            ],
            "postTokenBalances": [
                _balance(1, mint, source_owner, source_pre - amount),
                _balance(2, mint, destination_owner, destination_pre + amount),
            ],
            "innerInstructions": inner_rows,
            "logMessages": [],
        },
    }


def sale_tx(
    signature: str = "sale-signature",
    *,
    wallet: str = ROOT,
    amount: int = 1_000,
    block_time: int = 1_800_000_100,
    proceeds_lamports: int = 200_000_000,
    fee_lamports: int = 5_000,
    token_program: str = TOKEN_PROGRAM,
) -> dict[str, Any]:
    tx = token_transfer_tx(
        signature,
        source_owner=wallet,
        destination_owner=POOL,
        mint=TARGET_MINT,
        amount=amount,
        block_time=block_time,
        token_program=token_program,
        inner=True,
        outer_program=ROUTER,
    )
    tx["meta"]["preBalances"][0] = 1_000_000_000
    tx["meta"]["postBalances"][0] = 1_000_000_000 + proceeds_lamports - fee_lamports
    tx["meta"]["fee"] = fee_lamports
    tx["meta"]["logMessages"] = ["Program log: Instruction: Sell", "Program log: Instruction: Swap"]
    return tx


def buy_tx(
    signature: str = "buy-signature",
    *,
    wallet: str = ROOT,
    amount: int = 100,
    block_time: int = 1_800_000_200,
    cost_lamports: int = 20_000_000,
) -> dict[str, Any]:
    tx = token_transfer_tx(
        signature,
        source_owner=POOL,
        destination_owner=wallet,
        mint=TARGET_MINT,
        amount=amount,
        block_time=block_time,
        inner=True,
        outer_program=ROUTER,
    )
    tx["transaction"]["message"]["accountKeys"][0]["signer"] = False
    tx["transaction"]["message"]["accountKeys"][3]["signer"] = True
    keys = tx["transaction"]["message"]["accountKeys"]
    wallet_index = next(index for index, row in enumerate(keys) if row["pubkey"] == wallet)
    tx["meta"]["preBalances"][wallet_index] = 1_000_000_000
    tx["meta"]["postBalances"][wallet_index] = 1_000_000_000 - cost_lamports - 5_000
    tx["meta"]["logMessages"] = ["Program log: Instruction: Buy", "Program log: Instruction: Swap"]
    return tx


def sol_transfer_tx(
    signature: str,
    *,
    source: str,
    destination: str,
    lamports: int = 10_000_000,
    block_time: int = 1_800_000_000,
) -> dict[str, Any]:
    fee = 5_000
    return {
        "blockTime": block_time,
        "transaction": {
            "signatures": [signature],
            "message": {
                "accountKeys": [
                    {"pubkey": source, "signer": True},
                    {"pubkey": destination, "signer": False},
                    {"pubkey": SYSTEM_PROGRAM, "signer": False},
                ],
                "instructions": [
                    {
                        "programId": SYSTEM_PROGRAM,
                        "parsed": {
                            "type": "transfer",
                            "info": {"source": source, "destination": destination, "lamports": lamports},
                        },
                    }
                ],
            },
        },
        "meta": {
            "err": None,
            "fee": fee,
            "preBalances": [1_000_000_000, 1_000_000, 0],
            "postBalances": [1_000_000_000 - lamports - fee, 1_000_000 + lamports, 0],
            "preTokenBalances": [],
            "postTokenBalances": [],
            "innerInstructions": [],
            "logMessages": [],
        },
    }


def signed_only_tx(signature: str, wallet: str, *, block_time: int) -> dict[str, Any]:
    return {
        "blockTime": block_time,
        "transaction": {
            "signatures": [signature],
            "message": {
                "accountKeys": [
                    {"pubkey": wallet, "signer": True},
                    {"pubkey": COMPUTE_BUDGET_PROGRAM, "signer": False},
                ],
                "instructions": [{"programId": COMPUTE_BUDGET_PROGRAM, "data": "noop"}],
            },
        },
        "meta": {
            "err": None,
            "fee": 5_000,
            "preBalances": [1_000_000, 0],
            "postBalances": [995_000, 0],
            "preTokenBalances": [],
            "postTokenBalances": [],
            "innerInstructions": [],
            "logMessages": [],
        },
    }


class FakeProvider(history.HistoricalProvider):
    def __init__(
        self,
        *,
        target_transactions: list[dict[str, Any]],
        activity: dict[str, list[dict[str, Any]]],
        balances: dict[str, int],
        complete: bool = True,
        incomplete_wallets: set[str] | None = None,
    ) -> None:
        self.target_transactions = target_transactions
        self.activity = activity
        self.balances = balances
        self.complete = complete
        self.incomplete_wallets = incomplete_wallets or set()
        self.activity_calls: list[str] = []
        self.calls: list[tuple[str, str]] = []

    def _batch(self, transactions, *, complete=True, scope="fixture"):
        return history.ProviderBatch(list(transactions), complete, "fixture", scope, "deterministic offline fixture", 1)

    def get_token_events(self, mint, start, end):
        self.calls.append(("token_events", mint))
        return self._batch(self.target_transactions, complete=self.complete, scope="target token fixture")

    def get_wallet_events(self, wallet, mint, start, end):
        self.calls.append(("wallet_events", wallet))
        return self._batch(
            self.activity.get(wallet, []),
            complete=self.complete and wallet not in self.incomplete_wallets,
            scope="seed wallet fixture",
        )

    def get_wallet_activity(self, wallet, start, end):
        self.activity_calls.append(wallet)
        self.calls.append(("wallet_activity", wallet))
        return self._batch(
            self.activity.get(wallet, []),
            complete=self.complete and wallet not in self.incomplete_wallets,
            scope="all asset fixture",
        )

    def get_transaction(self, signature):
        for transaction in self.target_transactions + [row for rows in self.activity.values() for row in rows]:
            if history.transaction_signature(transaction) == signature:
                return transaction
        return None

    def get_wallet_token_balance(self, wallet, mint):
        return self.balances.get(wallet, 0)


class FakeRpc:
    def __init__(self, balances: dict[str, int], records: dict[str, dict[str, Any]] | None = None):
        self.balances = balances
        self.records = records or {wallet: account_record() for wallet in balances}
        self.balance_calls: list[str] = []

    def get_multiple_accounts(self, addresses):
        return {address: self.records.get(address) for address in addresses}

    def get_owner_token_accounts(self, wallet, mint, commitment="finalized"):
        self.balance_calls.append(wallet)
        return TokenBalanceSnapshot({f"acct-{wallet}": self.balances.get(wallet, 0)}, 12345)
