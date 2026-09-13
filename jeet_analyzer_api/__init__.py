"""Local read-only API wrapper for the frozen Jeet Analyzer engine."""

from .app import create_app

__all__ = ["create_app"]
