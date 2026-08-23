"""Authorization: allowlist ketat dari settings (fail-closed)."""

from __future__ import annotations

from seith_core.config import AppSettings, get_settings


def is_authorized(user_id: int | None, settings: AppSettings | None = None) -> bool:
    """False untuk None, non-allowlist, atau allowlist kosong - tidak ada jalur bypass."""
    s = settings or get_settings()
    if user_id is None:
        return False
    return user_id in s.telegram.allowed_user_ids
