"""Backward-compat shim — read_app_usage now reads from our own active-app
log database (app_logger.py), since knowledgeC.db stopped getting written
to on macOS Sonoma+.

Kept as a separate file so print_session.py / preview_receipt.py imports
don't need to change.
"""
from __future__ import annotations

from .app_logger import AppUsage, read_app_usage  # noqa: F401

__all__ = ["AppUsage", "read_app_usage"]
