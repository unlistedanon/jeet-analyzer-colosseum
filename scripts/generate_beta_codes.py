"""Generate invite/admin credentials into ignored local operator artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import secrets


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate high-entropy Jeet beta invite credentials locally.")
    parser.add_argument("--count", type=int, default=20, help="Invite count (default: 20; maximum: 100).")
    parser.add_argument("--output-dir", type=Path, default=Path("build/beta-admin"), help="Ignored private operator directory.")
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 100:
        parser.error("--count must be between 1 and 100")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.output_dir / f"invite-codes-{timestamp}-{suffix}.txt"
    admin_path = args.output_dir / f"admin-code-{timestamp}-{suffix}.txt"
    environment_path = args.output_dir / f"beta-secrets-{timestamp}-{suffix}.env"

    invites = [(f"beta-{index:02d}", f"jeet-beta-{secrets.token_urlsafe(24)}") for index in range(1, args.count + 1)]
    admin = f"jeet-admin-{secrets.token_urlsafe(32)}"
    session_secret = secrets.token_urlsafe(48)
    hashes = ",".join(f"{identifier}:{_digest(code)}" for identifier, code in invites)

    raw_lines = [
        "JEET ANALYZER PRIVATE BETA INVITES",
        "Share only one invite code per tester. Never commit this file.",
        "",
        *(f"{identifier}: {code}" for identifier, code in invites),
    ]
    admin_lines = [
        "JEET ANALYZER PRIVATE ADMIN CREDENTIAL",
        "Never share this value with beta testers. Never commit this file.",
        "",
        f"admin: {admin}",
    ]
    environment_lines = [
        "# Private server configuration. Never commit this file.",
        f"JEET_BETA_CODE_HASHES={hashes}",
        f"JEET_BETA_ADMIN_CODE_HASH={_digest(admin)}",
        f"JEET_BETA_SESSION_SECRET={session_secret}",
    ]
    with raw_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(raw_lines) + "\n")
    with admin_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(admin_lines) + "\n")
    with environment_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(environment_lines) + "\n")
    for path in (raw_path, admin_path, environment_path):
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Windows ACLs remain authoritative when POSIX mode bits are unavailable.
            pass

    print(f"Generated {args.count} invite codes plus one independent admin credential.")
    print(f"Raw invites (private): {raw_path.resolve()}")
    print(f"Raw admin credential (private): {admin_path.resolve()}")
    print(f"Hashed server configuration (private): {environment_path.resolve()}")
    print("No raw credential was printed to the console.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
