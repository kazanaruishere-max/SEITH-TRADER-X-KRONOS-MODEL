"""SQLite storage untuk EconomicEvent & NewsItem (E1 MATA).

Dedup EconomicEvent memakai natural key lintas-provider
(ticker, event_type, scheduled_at) - id provider TIDAK dipakai karena dua
provider bisa memberi id berbeda utk rilis yang sama. NewsItem dedup per
external_id.

Konvensi tulis mengikuti store.py: ATOMIK dan SINGLE-WRITER via BEGIN IMMEDIATE.
Payload disimpan sebagai JSON round-trip model_dump_json() - schema evolution
ditangani kontrak seith-core, bukan migrasi kolom per kolom.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from seith_core.config import AppSettings, get_settings
from seith_core.news_trigger import IMPORTANCE_RANK as _IMPORTANCE_RANK
from seith_core.schemas import (
    EconomicEvent,
    EventImportance,
    NewsItem,
)


def _require_aware_utc(ts: datetime, name: str) -> datetime:
    if ts.tzinfo is None:
        raise ValueError(f"'{name}' wajib timezone-aware (UTC), dapat datetime naive")
    return ts.astimezone(UTC)


def _connect(settings: AppSettings) -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_events_db(settings: AppSettings | None = None) -> None:
    s = settings or get_settings()
    with closing(_connect(s)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS economic_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                event_type TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                importance TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(ticker, event_type, scheduled_at)
            );
            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id TEXT NOT NULL,
                published_at TEXT NOT NULL,
                currencies TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(external_id)
            );
            CREATE INDEX IF NOT EXISTS idx_events_ticker_time
                ON economic_events(ticker, scheduled_at);
            CREATE INDEX IF NOT EXISTS idx_news_published
                ON news_items(published_at);
            """
        )


def upsert_economic_events(
    events: Sequence[EconomicEvent], settings: AppSettings | None = None
) -> int:
    """Tulis/replace event; return jumlah baris tersimpan total di tabel."""
    if not events:
        s0 = settings or get_settings()
        return count_economic_events(s0)
    s = settings or get_settings()
    init_events_db(s)
    now = datetime.now(UTC).isoformat()
    with closing(_connect(s)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                """
                INSERT INTO economic_events
                    (ticker, event_type, scheduled_at, importance, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, event_type, scheduled_at)
                DO UPDATE SET
                    importance = excluded.importance,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        e.ticker,
                        e.event_type,
                        e.scheduled_at.isoformat(),
                        e.importance.value,
                        e.model_dump_json(),
                        now,
                    )
                    for e in events
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return count_economic_events(settings)


def load_economic_events(
    ticker: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    min_importance: EventImportance | None = None,
    limit: int | None = None,
    settings: AppSettings | None = None,
) -> list[EconomicEvent]:
    """Query event terurut waktu naik; filter opsional per kriteria."""
    s = settings or get_settings()
    init_events_db(s)
    clauses: list[str] = []
    params: list[Any] = []
    if ticker is not None:
        clauses.append("ticker = ?")
        params.append(ticker.upper())
    if start is not None:
        clauses.append("scheduled_at >= ?")
        params.append(_require_aware_utc(start, "start").isoformat())
    if end is not None:
        clauses.append("scheduled_at <= ?")
        params.append(_require_aware_utc(end, "end").isoformat())
    if min_importance is not None:
        rank = _IMPORTANCE_RANK[min_importance]
        allowed = [i.value for i, r in _IMPORTANCE_RANK.items() if r >= rank]
        clauses.append(f"importance IN ({','.join('?' * len(allowed))})")
        params.extend(allowed)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT payload FROM economic_events{where} ORDER BY scheduled_at ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with closing(_connect(s)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [EconomicEvent.model_validate_json(r["payload"]) for r in rows]


def count_economic_events(settings: AppSettings | None = None) -> int:
    s = settings or get_settings()
    init_events_db(s)
    with closing(_connect(s)) as conn:
        return int(conn.execute("SELECT COUNT(*) AS n FROM economic_events").fetchone()["n"])


def upsert_news_items(
    items: Sequence[NewsItem], settings: AppSettings | None = None
) -> int:
    """Tulis/replace berita; return jumlah baris tersimpan total di tabel."""
    if not items:
        s0 = settings or get_settings()
        return count_news_items(s0)
    s = settings or get_settings()
    init_events_db(s)
    now = datetime.now(UTC).isoformat()
    with closing(_connect(s)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                """
                INSERT INTO news_items
                    (external_id, published_at, currencies, payload, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(external_id)
                DO UPDATE SET
                    published_at = excluded.published_at,
                    currencies = excluded.currencies,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        i.external_id,
                        i.published_at.isoformat(),
                        ",".join(i.currencies),
                        i.model_dump_json(),
                        now,
                    )
                    for i in items
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return count_news_items(settings)


def load_news_items(
    currency: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int | None = None,
    settings: AppSettings | None = None,
) -> list[NewsItem]:
    """Query berita terurut publikasi menurun (terbaru dulu); filter opsional."""
    s = settings or get_settings()
    init_events_db(s)
    clauses: list[str] = []
    params: list[Any] = []
    if currency is not None:
        code = currency.strip().upper()
        clauses.append("(',' || currencies || ',') LIKE ?")
        params.append(f"%,{code},%")
    if start is not None:
        clauses.append("published_at >= ?")
        params.append(_require_aware_utc(start, "start").isoformat())
    if end is not None:
        clauses.append("published_at <= ?")
        params.append(_require_aware_utc(end, "end").isoformat())
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT payload FROM news_items{where} ORDER BY published_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with closing(_connect(s)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [NewsItem.model_validate_json(r["payload"]) for r in rows]


def count_news_items(settings: AppSettings | None = None) -> int:
    s = settings or get_settings()
    init_events_db(s)
    with closing(_connect(s)) as conn:
        return int(conn.execute("SELECT COUNT(*) AS n FROM news_items").fetchone()["n"])
