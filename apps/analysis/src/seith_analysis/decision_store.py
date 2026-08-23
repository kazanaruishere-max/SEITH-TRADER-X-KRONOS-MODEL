"""Persistensi Decision ke SQLite (tabel `decisions` di db_path settings).

Decision log = audit trail lengkap (PRD FR-A4): setiap analisis tercatat
dengan reasoning, laporan agen, dan referensi forecast.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from seith_core.config import AppSettings, get_settings
from seith_core.schemas import Decision

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL NOT NULL,
    reasoning_summary TEXT NOT NULL,
    risk_assessment TEXT NOT NULL,
    forecast_id TEXT,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_ticker_date
    ON decisions (ticker, trade_date DESC);
"""


def _connect(settings: AppSettings) -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_decision_db(settings: AppSettings | None = None) -> None:
    s = settings or get_settings()
    with closing(_connect(s)) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def save_decision(decision: Decision, settings: AppSettings | None = None) -> None:
    s = settings or get_settings()
    init_decision_db(s)
    with closing(_connect(s)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO decisions"
            " (decision_id, ticker, asset_class, trade_date, action, confidence,"
            "  reasoning_summary, risk_assessment, forecast_id, raw_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision.decision_id,
                decision.ticker,
                decision.asset_class.value,
                decision.trade_date.isoformat(),
                decision.action.value,
                decision.confidence,
                decision.reasoning_summary,
                decision.risk_assessment,
                decision.forecast_id,
                decision.model_dump_json(),
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()


def load_decision(decision_id: str, settings: AppSettings | None = None) -> Decision | None:
    s = settings or get_settings()
    init_decision_db(s)
    with closing(_connect(s)) as conn:
        row = conn.execute(
            "SELECT raw_json FROM decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
    return Decision.model_validate_json(row["raw_json"]) if row else None


def load_recent(ticker: str | None = None, limit: int = 10, settings: AppSettings | None = None):
    s = settings or get_settings()
    init_decision_db(s)
    query = "SELECT decision_id, ticker, action, confidence, created_at FROM decisions"
    params: list[object] = []
    if ticker:
        query += " WHERE ticker = ?"
        params.append(ticker.upper())
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with closing(_connect(s)) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def export_json(decision: Decision, out_dir=None) -> str:
    """Tulis salinan JSON human-readable di bawah data dir; return path relatif."""
    s = get_settings() if out_dir is None else None
    base = out_dir or (s.data_dir / "decisions")
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{decision.decision_id}.json"
    path.write_text(json.dumps(json.loads(decision.model_dump_json()), indent=2), encoding="utf-8")
    return str(path)
