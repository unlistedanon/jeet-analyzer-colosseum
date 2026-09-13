"""Build an allowlisted, sanitized Eternal Week 1 review package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Iterable
from urllib.parse import parse_qs, urlparse


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "build" / "eternal-week1" / "jeet-analyzer-week1"
ROOT_FILES = ("LICENSE", "pyproject.toml", "requirements.txt", "REVIEWER_START_HERE.md")
SOURCE_DIRECTORIES = ("jeet_analyzer", "jeet_analyzer_api", "schemas", "tests")
FRONTEND_FILES = (
    "frontend/index.html",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.json",
    "frontend/tsconfig.node.json",
    "frontend/vite.config.ts",
)
DOCUMENTS = (
    "docs/ARCHITECTURE.md",
    "docs/COMMANDS.md",
    "docs/ETERNAL_BUILD_LOG.md",
    "docs/COST_PROTECTION.md",
    "docs/EVIDENCE_MODEL.md",
    "docs/PROVIDER_BEHAVIOR.md",
    "docs/PUBLIC_CASE_EXPORT.md",
    "docs/RESULT_SCHEMA.md",
    "docs/UI.md",
)
DEMO_FILES = (
    "demo/flagship-evidence.v1.json",
    "scripts/demo_flagship.py",
    "scripts/validate_all.py",
    "scripts/build_public_case.py",
    "scripts/build_external_review.py",
    "scripts/render_week1_video.py",
    "scripts/week1_video_media.ps1",
)
COMPETITION_FILES = (
    "competition/eternal-week1/README.md",
    "competition/eternal-week1/WEEK1_STORY.md",
    "competition/eternal-week1/VIDEO_PLAN.md",
    "competition/eternal-week1/week1-video.srt",
)
SKIP_PARTS = {"__pycache__", "node_modules", "dist", "build", "receipts", ".git", ".venv"}
TEXT_SUFFIXES = {".py", ".ps1", ".srt", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".toml", ".txt", ".html", ".css"}
SECRET_QUERY_NAMES = {"api_key", "apikey", "api-key", "token", "secret", "password", "auth"}
UNRELATED_PROJECT_MARKERS = ("Bot" + "ToTrot", "josh" + "-prize", "josh" + "nft", "JOSH" + " 420")


class PackageAuditError(RuntimeError):
    """Raised when the external-review package contains unsafe material."""


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _iter_directory(source: Path) -> Iterable[Path]:
    for path in source.rglob("*"):
        if path.is_file() and not any(part in SKIP_PARTS for part in path.parts):
            yield path


def _safe_output(output: Path) -> Path:
    resolved = output.resolve()
    build_root = (REPOSITORY_ROOT / "build").resolve()
    if not resolved.is_relative_to(build_root) or resolved == build_root:
        raise PackageAuditError("external package output must be a child of the repository build directory")
    return resolved


def _is_inert_example_url(value: str) -> bool:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    return hostname.endswith(".example") or hostname.endswith(".invalid")


def audit_package(package_root: Path, forbidden_values: Iterable[str] = ()) -> dict[str, object]:
    forbidden_values = tuple(forbidden_values)
    failures: list[str] = []
    inert_example_urls = 0
    inert_safety_test_mentions = 0
    files = [path for path in package_root.rglob("*") if path.is_file()]
    for path in files:
        relative = path.relative_to(package_root).as_posix()
        lower_parts = {part.lower() for part in path.parts}
        if ".env" in lower_parts or path.name.lower().startswith(".env"):
            failures.append(f"forbidden environment file: {relative}")
        if any(part in SKIP_PARTS for part in path.relative_to(package_root).parts):
            failures.append(f"forbidden package path: {relative}")
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"LICENSE"}:
            continue
        text = path.read_text(encoding="utf-8", errors="strict")
        if any(value and value in text for value in forbidden_values):
            failures.append(f"forbidden investigation identifier: {relative}")
        if re.search(r"(?i)(?:[A-Z]:\\Users\\[A-Za-z0-9._-]+\\|/Users/[A-Za-z0-9._-]+/|/home/[A-Za-z0-9._-]+/)", text):
            failures.append(f"personal absolute path: {relative}")
        if any(marker.lower() in text.lower() for marker in UNRELATED_PROJECT_MARKERS):
            if relative.startswith("tests/"):
                inert_safety_test_mentions += 1
            else:
                failures.append(f"unrelated project material: {relative}")
        if re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", text):
            failures.append(f"private key material: {relative}")
        if re.search(r"(?im)^\s*(?:HELIUS_API_KEY|SOLANA_RPC_URL|PRIVATE_KEY|SEED_PHRASE|MNEMONIC)\s*[:=]\s*(?![<{$%])\S+", text):
            failures.append(f"credential assignment: {relative}")
        for url in re.findall(r"(?:https?|wss?)://[^\s\"'<>]+", text, re.IGNORECASE):
            query_names = {name.lower() for name in parse_qs(urlparse(url).query)}
            if query_names & SECRET_QUERY_NAMES:
                if _is_inert_example_url(url):
                    inert_example_urls += 1
                else:
                    failures.append(f"authenticated URL: {relative}")
    if failures:
        raise PackageAuditError("; ".join(sorted(set(failures))))
    return {
        "status": "PASS",
        "files_scanned": len(files),
        "inert_example_url_fixtures": inert_example_urls,
        "inert_safety_test_mentions": inert_safety_test_mentions,
        "forbidden_values_checked": len(forbidden_values),
    }


def build_package(output: Path = DEFAULT_OUTPUT, forbidden_values: Iterable[str] = ()) -> tuple[Path, Path, dict[str, object]]:
    destination = _safe_output(output)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for relative in ROOT_FILES + FRONTEND_FILES + DOCUMENTS + DEMO_FILES + COMPETITION_FILES:
        _copy_file(REPOSITORY_ROOT / relative, destination / relative)
    _copy_file(
        REPOSITORY_ROOT / "competition" / "eternal-week1" / "README.md",
        destination / "README.md",
    )
    for directory in SOURCE_DIRECTORIES:
        source_root = REPOSITORY_ROOT / directory
        for source in _iter_directory(source_root):
            _copy_file(source, destination / source.relative_to(REPOSITORY_ROOT))
    for source in _iter_directory(REPOSITORY_ROOT / "frontend" / "src"):
        _copy_file(source, destination / source.relative_to(REPOSITORY_ROOT))
    # Hosted SEO now contains the live site origin. The external review is an
    # inert package: replace only that exact origin in these copied files.
    # Keep the source website and the package's strict audit unchanged.
    live_origin = "https://jeet.example"
    for relative in ("frontend/index.html", "jeet_analyzer_api/search_public.py"):
        copied = destination / relative
        copied.write_text(copied.read_text(encoding="utf-8").replace(live_origin, "https://jeet.example"), encoding="utf-8")
    audit = audit_package(destination, forbidden_values)
    manifest_files = []
    for path in sorted(item for item in destination.rglob("*") if item.is_file()):
        relative = path.relative_to(destination).as_posix()
        manifest_files.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {
        "schema": "jeet-analyzer.external-review-manifest.v1",
        "package": "Jeet Analyzer Eternal Week 1",
        "sanitization": audit,
        "files": manifest_files,
    }
    (destination / "PACKAGE-MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    archive_base = destination.parent / destination.name
    archive_path = Path(shutil.make_archive(str(archive_base), "zip", root_dir=destination))
    return destination, archive_path, audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the sanitized Eternal Week 1 external-review package")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    package, archive, audit = build_package(args.output)
    print("EXTERNAL_REVIEW_AUDIT=PASS")
    print(f"FILES_SCANNED={audit['files_scanned']}")
    print(f"PACKAGE={package.relative_to(REPOSITORY_ROOT).as_posix()}")
    print(f"ARCHIVE={archive.relative_to(REPOSITORY_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
