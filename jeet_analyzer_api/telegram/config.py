from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from urllib.parse import urlparse


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool = False
    token: str = field(default="", repr=False)
    webhook_secret: str = field(default="", repr=False)
    mode: str = "webhook"
    username: str = ""
    public_base_url: str = ""
    user_per_minute: int = 5
    chat_per_minute: int = 12
    global_per_minute: int = 30
    inbox_limit: int = 100
    job_timeout_seconds: int = 900
    stop_file: Path = Path("build/telegram.STOP")

    @classmethod
    def from_env(cls):
        if os.getenv("JEET_TELEGRAM_ENABLED", "0") != "1":
            return cls()
        return cls(enabled=True, token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                   webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", ""),
                   mode=os.getenv("JEET_TELEGRAM_MODE", "webhook"),
                   username=os.getenv("JEET_TELEGRAM_USERNAME", ""),
                   public_base_url=os.getenv("JEET_PUBLIC_BASE_URL", ""),
                   user_per_minute=int(os.getenv("TELEGRAM_RATE_LIMIT_USER", "5")),
                   chat_per_minute=int(os.getenv("TELEGRAM_RATE_LIMIT_CHAT", "12")),
                   global_per_minute=int(os.getenv("TELEGRAM_RATE_LIMIT_GLOBAL", "30")),
                   inbox_limit=int(os.getenv("JEET_TELEGRAM_INBOX_LIMIT", "100")),
                   job_timeout_seconds=int(os.getenv("JEET_TELEGRAM_JOB_TIMEOUT_SECONDS", "900")),
                   stop_file=Path(os.getenv("JEET_TELEGRAM_STOP_FILE", "build/telegram.STOP")))

    def validate(self):
        if not self.enabled:
            return
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", self.token):
            raise ValueError("Configure TELEGRAM_BOT_TOKEN privately")
        if self.mode not in {"polling", "webhook"}:
            raise ValueError("Telegram mode must be polling or webhook")
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", self.username):
            raise ValueError("Configure the Telegram bot username without @")
        u = urlparse(self.public_base_url)
        if u.scheme != "https" or not u.netloc or u.username or u.query or u.fragment or u.path not in {"", "/"}:
            raise ValueError("JEET_PUBLIC_BASE_URL must be an HTTPS origin")
        if self.mode == "webhook" and not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", self.webhook_secret):
            raise ValueError("Configure a random webhook secret of at least 32 characters")
        if min(self.user_per_minute, self.chat_per_minute, self.global_per_minute, self.inbox_limit, self.job_timeout_seconds) < 1:
            raise ValueError("Telegram limits must be positive")
