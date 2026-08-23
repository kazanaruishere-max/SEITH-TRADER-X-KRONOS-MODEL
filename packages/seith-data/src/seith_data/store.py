"""Parquet storage + SQLite metadata untuk OHLCV.

Konvensi DataFrame OHLCV di seluruh SEITH (ditegakkan di save_ohlcv):
- Index: DatetimeIndex UTC tz-aware, nama `timestamp`
- Kolom persis: open, high, low, close, volume (float64)

Kebijakan tulis: ATOMIK (tmp + os.replace) dan SINGLE-WRITER per database
(transaksi BEGIN IMMEDIATE sebagai mutex lintas proses untuk read-modify-write).
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import TypeAdapter
from seith_core.config import AppSettings, get_settings
from seith_core.schemas import Ticker, Timeframe

OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
_validate_ticker = TypeAdapter(Ticker)


def ohlcv_path(settings: AppSettings, ticker: str, timeframe: Timeframe) -> Path:
    safe_ticker = _validate_ticker.validate_python(ticker)
    return settings.data_dir / "parquet" / safe_ticker / f"{timeframe.value}.parquet"


def _connect(settings: AppSettings) -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def writer_lock(settings: AppSettings | None = None) -> Iterator[None]:
    """Mutex lintas-proses untuk operasi read-modify-write (upsert).

    Memakai BEGIN IMMEDIATE di SQLite bersama: proses kedua yang mencoba
    menulis akan blocking sampai transaksi pertama commit.
    """
    s = settings or get_settings()
    init_db(s)
    with closing(_connect(s)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def init_db(settings: AppSettings | None = None) -> None:
    s = settings or get_settings()
    with closing(_connect(s)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ingestion_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                source TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                rows_written INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS quality_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER REFERENCES ingestion_runs(id),
                ticker TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                check_name TEXT NOT NULL,
                severity TEXT NOT NULL,
                detail TEXT NOT NULL,
                detected_at TEXT NOT NULL
            );
            """
        )


def start_run(
    ticker: str, timeframe: Timeframe, source: str, settings: AppSettings | None = None
) -> int:
    s = settings or get_settings()
    init_db(s)
    with closing(_connect(s)) as conn:
        cur = conn.execute(
            "INSERT INTO ingestion_runs (ticker, timeframe, source, started_at)"
            " VALUES (?, ?, ?, ?)",
            (
                ticker.upper(),
                timeframe.value,
                source,
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def finish_run(
    run_id: int,
    rows_written: int,
    error: str | None = None,
    settings: AppSettings | None = None,
) -> None:
    s = settings or get_settings()
    status = "ok" if error is None else "failed"
    with closing(_connect(s)) as conn:
        conn.execute(
            "UPDATE ingestion_runs SET finished_at = ?, rows_written = ?, status = ?, error = ?"
            " WHERE id = ?",
            (
                datetime.now(UTC).isoformat(),
                rows_written,
                status,
                error,
                run_id,
            ),
        )
        conn.commit()


def record_findings(
    run_id: int,
    ticker: str,
    timeframe: Timeframe,
    findings: list[dict[str, Any]],
    settings: AppSettings | None = None,
) -> None:
    if not findings:
        return
    s = settings or get_settings()
    now = datetime.now(UTC).isoformat()
    rows = [
        (
            run_id,
            ticker.upper(),
            timeframe.value,
            f["check_name"],
            f["severity"],
            f["detail"],
            now,
        )
        for f in findings
    ]
    with closing(_connect(s)) as conn:
        conn.executemany(
            "INSERT INTO quality_findings"
            " (run_id, ticker, timeframe, check_name, severity, detail, detected_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()


def _enforce_contract(df: pd.DataFrame) -> None:
    if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
        raise ValueError("OHLCV index wajib DatetimeIndex UTC tz-aware")
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing or len(df.columns) != len(OHLCV_COLUMNS):
        raise ValueError(f"kolom OHLCV wajib persis {OHLCV_COLUMNS}; missing={missing}")
    for col in OHLCV_COLUMNS:
        if not pd.api.types.is_float_dtype(df[col]):
            raise ValueError(f"kolom '{col}' wajib float64; dapat {df[col].dtype}")


def write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    """Tulis parquet secara atomik (tmp + os.replace) - aman terhadap crash & pembaca konkuren."""
    tmp = path.with_suffix(f".tmp-{uuid.uuid4().hex}.parquet")
    try:
        df.to_parquet(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def save_ohlcv(
    df: pd.DataFrame,
    ticker: str,
    timeframe: Timeframe,
    settings: AppSettings | None = None,
) -> Path:
    """Tulis penuh (replace, atomik). Untuk append gunakan upsert_ohlcv."""
    _enforce_contract(df)
    s = settings or get_settings()
    path = ohlcv_path(s, ticker, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_parquet_atomic(df.sort_index(), path)
    return path


def load_ohlcv(
    ticker: str, timeframe: Timeframe, settings: AppSettings | None = None
) -> pd.DataFrame | None:
    s = settings or get_settings()
    path = ohlcv_path(s, ticker, timeframe)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    # parquet tidak menyimpan attrs -> inject agar konsumen selalu punya konteks
    df.attrs["timeframe"] = timeframe.value
    return df


def upsert_ohlcv(
    df_new: pd.DataFrame,
    ticker: str,
    timeframe: Timeframe,
    settings: AppSettings | None = None,
) -> int:
    """Gabungkan data baru dengan existing di bawah writer-lock.

    Duplikat timestamp menang versi baru (keep-last). Return total baris tersimpan.
    """
    _enforce_contract(df_new)
    with writer_lock(settings):
        existing = load_ohlcv(ticker, timeframe, settings)
        if existing is not None and not existing.empty:
            combined = pd.concat([existing, df_new])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined = combined.sort_index()
        else:
            combined = df_new.sort_index()
        s = settings or get_settings()
        path = ohlcv_path(s, ticker, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_parquet_atomic(combined, path)
        return len(combined)
