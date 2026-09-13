"""Environment-backed configuration for the invite-only beta boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path


def _boolean(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _integer(name: str, default: int, *, minimum: int = 1) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _csv(name: str) -> tuple[str, ...]:
    value = os.environ.get(name, "")
    return tuple(item.strip().lower() for item in value.split(",") if item.strip())


def hash_secret(value: str) -> str:
    """Hash a high-entropy beta/admin code without retaining the raw value."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_code_hashes(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        identifier, separator, digest = entry.partition(":")
        identifier = identifier.strip()
        digest = digest.strip().lower()
        if not separator or not identifier or len(digest) != 64:
            raise ValueError("JEET_BETA_CODE_HASHES must contain code-id:sha256 entries")
        if any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("JEET_BETA_CODE_HASHES contains a non-hex SHA-256 digest")
        result[identifier] = digest
    return result


@dataclass(frozen=True)
class BetaConfig:
    enabled: bool
    code_hashes: dict[str, str]
    admin_code_hash: str | None
    session_secret: str
    session_ttl_seconds: int
    cookie_secure: bool
    max_concurrent: int
    max_runs_per_code_per_day: int
    max_estimated_credits_per_run: int
    global_daily_credit_cap: int
    database_path: Path
    output_root: Path
    frontend_dist: Path
    public_access: bool = False
    max_estimated_credits_per_code_per_day: int | None = None
    days: float = 30.0
    graph_depth: int = 3
    max_wallets: int = 25
    max_pages: int = 200
    request_timeout: float = 45.0
    provider_retries: int = 1
    provider_backoff_cap: float = 8.0
    max_rpc_requests: int = 1_500
    max_signatures: int = 40_000
    max_transactions: int = 20_000
    funding_lookback_days: int = 365
    materiality_inventory_pct: float = 0.01
    deep_forensic: bool = True
    allowed_hosts: tuple[str, ...] = ()
    public_origin: str | None = None
    invite_login_failures: int = 10
    admin_login_failures: int = 5
    login_window_seconds: int = 300

    def __post_init__(self) -> None:
        if self.global_daily_credit_cap < self.max_estimated_credits_per_run:
            raise ValueError(
                "JEET_BETA_GLOBAL_DAILY_CREDIT_CAP must cover one investigation ceiling"
            )
        if not 0 <= self.graph_depth <= 3:
            raise ValueError("JEET_BETA_GRAPH_DEPTH must be between 0 and 3")
        if self.public_origin and not self.public_origin.startswith("https://"):
            raise ValueError("JEET_BETA_PUBLIC_ORIGIN must use https:// when configured")
        if any("/" in host or " " in host for host in self.allowed_hosts):
            raise ValueError("JEET_BETA_ALLOWED_HOSTS must contain hostnames only")

    @classmethod
    def from_env(cls, root: Path | None = None) -> "BetaConfig":
        repository = root or Path(__file__).resolve().parents[1]
        data_root = Path(os.environ.get("JEET_BETA_DATA_DIR", repository / "build" / "beta-data"))
        session_secret = os.environ.get("JEET_BETA_SESSION_SECRET", "")
        public_origin = os.environ.get("JEET_BETA_PUBLIC_ORIGIN", "").strip() or None
        return cls(
            enabled=_boolean("JEET_BETA_ENABLED", False),
            public_access=_boolean("JEET_BETA_PUBLIC_ACCESS", True),
            code_hashes=parse_code_hashes(os.environ.get("JEET_BETA_CODE_HASHES", "")),
            admin_code_hash=os.environ.get("JEET_BETA_ADMIN_CODE_HASH") or None,
            session_secret=session_secret,
            session_ttl_seconds=_integer("JEET_BETA_SESSION_TTL_SECONDS", 28_800, minimum=60),
            cookie_secure=_boolean("JEET_BETA_COOKIE_SECURE", False),
            max_concurrent=_integer("JEET_BETA_MAX_CONCURRENT", 1),
            max_runs_per_code_per_day=_integer("JEET_BETA_MAX_RUNS_PER_CODE_PER_DAY", 1),
            max_estimated_credits_per_run=_integer("JEET_BETA_MAX_ESTIMATED_CREDITS_PER_RUN", 60_000),
            global_daily_credit_cap=_integer("JEET_BETA_GLOBAL_DAILY_CREDIT_CAP", 250_000),
            database_path=Path(os.environ.get("JEET_BETA_DB_PATH", data_root / "jeet-beta.sqlite3")),
            output_root=Path(os.environ.get("JEET_BETA_OUTPUT_DIR", data_root / "investigations")),
            frontend_dist=Path(os.environ.get("JEET_BETA_FRONTEND_DIST", repository / "frontend" / "dist")),
            days=float(os.environ.get("JEET_BETA_DAYS", "30")),
            graph_depth=_integer("JEET_BETA_GRAPH_DEPTH", 3, minimum=0),
            max_wallets=_integer("JEET_BETA_MAX_WALLETS", 25),
            max_pages=_integer("JEET_BETA_MAX_PAGES", 200),
            request_timeout=float(os.environ.get("JEET_BETA_REQUEST_TIMEOUT", "45")),
            provider_retries=_integer("JEET_BETA_PROVIDER_RETRIES", 1, minimum=0),
            provider_backoff_cap=float(os.environ.get("JEET_BETA_PROVIDER_BACKOFF_CAP", "8")),
            max_rpc_requests=_integer("JEET_BETA_MAX_RPC_REQUESTS", 1_500),
            max_signatures=_integer("JEET_BETA_MAX_SIGNATURES", 40_000),
            max_transactions=_integer("JEET_BETA_MAX_TRANSACTIONS", 20_000),
            funding_lookback_days=_integer("JEET_BETA_FUNDING_LOOKBACK_DAYS", 365),
            materiality_inventory_pct=float(os.environ.get("JEET_BETA_MATERIALITY_INVENTORY_PCT", "0.01")),
            deep_forensic=_boolean("JEET_BETA_DEEP_FORENSIC", True),
            allowed_hosts=_csv("JEET_BETA_ALLOWED_HOSTS"),
            public_origin=public_origin,
            invite_login_failures=_integer("JEET_BETA_INVITE_LOGIN_FAILURES", 10),
            admin_login_failures=_integer("JEET_BETA_ADMIN_LOGIN_FAILURES", 5),
            login_window_seconds=_integer("JEET_BETA_LOGIN_WINDOW_SECONDS", 300, minimum=30),
        )

    @property
    def configured(self) -> bool:
        return bool((self.public_access or self.code_hashes) and self.admin_code_hash and len(self.session_secret) >= 32)

    def investigation_request(self, mint: str, wallet: str) -> dict[str, object]:
        """Build server-owned limits; beta clients cannot raise provider budgets."""

        return {
            "mint": mint,
            "wallet": wallet,
            "days": self.days,
            "graph_depth": self.graph_depth,
            "max_wallets": self.max_wallets,
            "max_pages": self.max_pages,
            "request_timeout": self.request_timeout,
            "provider_retries": self.provider_retries,
            "provider_backoff_cap": self.provider_backoff_cap,
            "max_rpc_requests": self.max_rpc_requests,
            "max_signatures": self.max_signatures,
            "max_transactions": self.max_transactions,
            "max_estimated_provider_credits": self.max_estimated_credits_per_run,
            "funding_lookback_days": self.funding_lookback_days,
            "materiality_inventory_pct": self.materiality_inventory_pct,
            "deep_forensic": self.deep_forensic,
        }
