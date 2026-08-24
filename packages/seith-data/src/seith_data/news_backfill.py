"""Orkestrasi backfill event kalender (E1 MATA).

Finnhub dibatasi rentang permintaan; chunk bulanan aman untuk backfill 1+ tahun.
Setiap chunk langsung di-upsert sehingga kegagalan di tengah tidak membuang
progress. Return total baris tersimpan setelah seluruh chunk selesai.
"""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date

from seith_core.config import AppSettings, get_settings

from seith_data.events_store import count_economic_events, upsert_economic_events
from seith_data.sources.economic_calendar import fetch_finnhub_calendar

logger = logging.getLogger(__name__)


def _month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Pecah [start, end] menjadi potongan kalender bulanan."""
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        last_day = monthrange(cursor.year, cursor.month)[1]
        chunk_end = min(end, date(cursor.year, cursor.month, last_day))
        chunks.append((cursor, chunk_end))
        if chunk_end == end:
            break
        cursor = date(
            chunk_end.year + (1 if chunk_end.month == 12 else 0),
            1 if chunk_end.month == 12 else chunk_end.month + 1,
            1,
        )
    return chunks


def backfill_economic_events(
    start: date,
    end: date,
    api_key: str | None = None,
    settings: AppSettings | None = None,
) -> int:
    """Backfill rilis ekonomi [start, end] dari Finnhub ke events store.

    Return jumlah baris AKTUAL di tabel setelah backfill (bukan jumlah fetch -
    dedup natural key bisa menggabungkan duplikat antar-chunk).
    """
    if start > end:
        raise ValueError("start wajib <= end")
    s = settings or get_settings()
    for chunk_start, chunk_end in _month_chunks(start, end):
        events = fetch_finnhub_calendar(chunk_start, chunk_end, api_key=api_key)
        upsert_economic_events(events, settings=s)
        logger.info("chunk %s..%s: %d event fetched", chunk_start, chunk_end, len(events))
    return count_economic_events(s)
