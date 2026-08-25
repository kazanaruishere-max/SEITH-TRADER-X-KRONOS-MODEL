"""Authorization: allowlist ketat dari settings (fail-closed).

Role per user (B4):
  - OWNER  : allowed_user_ids[0] (funda); boleh eksekusi/approve/halt/mode.
  - VIEWER : allowlist lain; baca saja, ditolak command destruktif.
"""

from __future__ import annotations

from seith_core.config import AppSettings, get_settings

OWNER_INDEX = 0


def is_authorized(user_id: int | None, settings: AppSettings | None = None) -> bool:
    """False untuk None, non-allowlist, atau allowlist kosong - tidak ada jalur bypass."""
    s = settings or get_settings()
    if user_id is None:
        return False
    return user_id in s.telegram.allowed_user_ids


def is_owner(user_id: int | None, settings: AppSettings | None = None) -> bool:
    s = settings or get_settings()
    ids = s.telegram.allowed_user_ids
    return bool(ids) and user_id == ids[OWNER_INDEX]


def role_of(user_id: int | None, settings: AppSettings | None = None) -> str:
    if not is_authorized(user_id, settings):
        return "none"
    return "owner" if is_owner(user_id, settings) else "viewer"
