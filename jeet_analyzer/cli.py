"""Public command-line entry point."""

from __future__ import annotations

from typing import Sequence

from .analyzer import main as _main


def main(argv: Sequence[str] | None = None) -> int:
    return _main(argv)
