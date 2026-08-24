"""Saklar mode trading (E4 TANGAN): off | semi | auto - disimpan di shared DB.

Default SEMI (approval manusia wajib). Mode AUTO membuat proposal langsung
APPROVED dengan approved_by='system:auto' HANYA bila confidence >= threshold -
dan RiskManager TETAP menjadi gerbang terakhir saat intake (Tier-0, tidak
bisa dilewati mode apapun). OFF = news bridge tidak membuat proposal sama sekali.

Dipakai dua service: bot Telegram (apps/api) menulis, news bridge (apps/trader)
membaca - satu tabel, satu sumber kebenaran.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from seith_core.config import AppSettings, get_settings


class TradingMode(StrEnum):
    OFF = "off"
    SEMI = "semi"
    AUTO = "auto"


#: Batas aman notional per order news (USD) - placeholder konservatif; sizing
#: final tetap ditentukan RiskManager saat intake.
DEFAULT_AUTO_MIN_CONFIDENCE = 0.70
DEFAULT_NEWS_MAX_NOTIONAL_USD = Decimal("200")


def init_trading_mode_db(settings: AppSettings | None = None) -> None:
    s = settings or get_settings()
    s.ensure_dirs()
    with closing(_connect(s)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trading_mode (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                mode TEXT NOT NULL,
                auto_min_confidence REAL NOT NULL,
                max_notional_usd TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL
            );
            """
        )
        row = conn.execute("SELECT id FROM trading_mode WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO trading_mode (id, mode, auto_min_confidence,"
                " max_notional_usd, updated_at, updated_by)"
                " VALUES (1, 'semi', ?, ?, ?, 'system:default')",
                (
                    DEFAULT_AUTO_MIN_CONFIDENCE,
                    str(DEFAULT_NEWS_MAX_NOTIONAL_USD),
                    datetime_now_iso(),
                ),
            )
        conn.commit()


def _connect(settings: AppSettings) -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    # bridge baca tiap 60s x bot tulis /mode - anti "database is locked"
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def datetime_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_trading_mode(
    settings: AppSettings | None = None,
) -> tuple[TradingMode, float, Decimal]:
    """Baca (mode, auto_min_confidence, max_notional_usd); inisialisasi bila kosong."""
    s = settings or get_settings()
    init_trading_mode_db(s)
    with closing(_connect(s)) as conn:
        row = conn.execute(
            "SELECT mode, auto_min_confidence, max_notional_usd FROM trading_mode"
            " WHERE id = 1"
        ).fetchone()
    if row is None:  # init_trading_mode_db menjamin baris ada; guard eksplisit
        raise RuntimeError("trading_mode table kosong meski sudah di-init")
    return (
        TradingMode(row["mode"]),
        float(row["auto_min_confidence"]),
        Decimal(str(row["max_notional_usd"])),
    )


def set_trading_mode(
    mode: TradingMode,
    updated_by: str,
    auto_min_confidence: float | None = None,
    max_notional_usd: Decimal | None = None,
    settings: AppSettings | None = None,
) -> None:
    """Tulis mode baru; parameter opsional hanya berlaku utk AUTO."""
    s = settings or get_settings()
    init_trading_mode_db(s)
    with closing(_connect(s)) as conn:
        # baca-current + tulis dalam SATU transaksi (anti TOCTOU antar writer)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT auto_min_confidence, max_notional_usd FROM trading_mode"
                " WHERE id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("trading_mode table kosong meski sudah di-init")
            conn.execute(
                "UPDATE trading_mode SET mode = ?, auto_min_confidence = ?,"
                " max_notional_usd = ?, updated_at = ?, updated_by = ? WHERE id = 1",
                (
                    mode.value,
                    auto_min_confidence
                    if auto_min_confidence is not None
                    else float(row["auto_min_confidence"]),
                    str(max_notional_usd)
                    if max_notional_usd is not None
                    else str(Decimal(str(row["max_notional_usd"]))),
                    datetime_now_iso(),
                    updated_by,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
