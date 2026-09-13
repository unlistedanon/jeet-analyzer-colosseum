"""Validated API inputs and transport records for UI investigations."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jeet_analyzer.analyzer import validate_public_address


def _public_address(value: str) -> str:
    try:
        return validate_public_address(value)
    except Exception as exc:
        raise ValueError(str(exc)) from exc


class InvestigationMode(str, Enum):
    SELLER_SCAN = "seller-scan"
    WALLET_AUDIT = "wallet-audit"
    CLUSTER_AUDIT = "cluster-audit"


class InvestigationState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"


class BaseInvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mint: str
    days: float = Field(default=5.0, gt=0, le=3650)
    max_pages: int = Field(default=1000, ge=1, le=1000)
    request_timeout: float = Field(default=30.0, gt=0, le=300)
    provider_retries: int = Field(default=2, ge=0, le=10)
    provider_backoff_cap: float = Field(default=8.0, gt=0, le=60)
    max_rpc_requests: int = Field(default=500, ge=1, le=1_000_000)
    max_signatures: int = Field(default=10_000, ge=1, le=1_000_000)
    max_transactions: int = Field(default=5_000, ge=1, le=1_000_000)
    max_estimated_provider_credits: int = Field(default=60_000, ge=1)

    @field_validator("mint")
    @classmethod
    def validate_mint(cls, value: str) -> str:
        return _public_address(value)


class SellerScanRequest(BaseInvestigationRequest):
    pass


class WalletAuditRequest(BaseInvestigationRequest):
    wallet: str
    trace_depth: int = Field(default=1, ge=0, le=1)

    @field_validator("wallet")
    @classmethod
    def validate_wallet(cls, value: str) -> str:
        return _public_address(value)


class ClusterAuditRequest(BaseInvestigationRequest):
    days: float = Field(default=14.0, gt=0, le=3650)
    wallet: str
    # Default stays conservative for normal callers. Hunt Mode may explicitly
    # request deeper bounded traversal up to six hops.
    graph_depth: int = Field(default=3, ge=0, le=6)
    max_wallets: int = Field(default=50, ge=1, le=10_000)
    funding_lookback_days: int = Field(default=365, ge=1, le=3650)
    materiality_inventory_pct: float = Field(default=0.01, ge=0, le=100)
    deep_forensic: bool = False

    @field_validator("wallet")
    @classmethod
    def validate_wallet(cls, value: str) -> str:
        return _public_address(value)


class InvestigationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    mode: InvestigationMode
    state: InvestigationState
    created_at: str
    updated_at: str
    request: dict[str, Any]
    progress: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


class BetaInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_code: str = Field(min_length=12, max_length=256)


class BetaInvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mint: str
    wallet: str

    @field_validator("mint", "wallet")
    @classmethod
    def validate_address(cls, value: str) -> str:
        return _public_address(value)


class BetaFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    useful: bool | None = None
    comment: str = Field(default="", max_length=2_000)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str) -> str:
        return value.strip()
