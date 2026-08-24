"""Verifikasi cakupan data m1 per ticker - prasyarat spike-analysis (E2).

Pattern library butuh m1 kontinyu di window pasca-rilis; report ini menunjukkan
ticker mana yang datanya belum layak dipakai (kosong / gap besar).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pandas import Timestamp
from seith_core.config import AppSettings, get_settings
from seith_core.schemas import Timeframe

from seith_data.store import load_ohlcv


@dataclass(frozen=True)
class M1CoverageReport:
    """Ringkasan kualitas cakupan m1 satu ticker.

    CATATAN: expected_bars menghitung menit KALENDER antara first..last.
    Untuk aset yang pasarnya tutup malam/weekend (saham US), coverage_pct akan
    tampak rendah padahal data wajar - report ini diagnostik, BUKAN gate pass/fail.
    """

    ticker: str
    exists: bool
    bar_count: int
    first_at: datetime | None
    last_at: datetime | None
    expected_bars: int  # jumlah bar ideal antara first..last @60 detik
    coverage_pct: float  # bar_count / expected_bars * 100 (0 bila kosong)


def m1_coverage_report(
    tickers: list[str], settings: AppSettings | None = None
) -> tuple[M1CoverageReport, ...]:
    s = settings or get_settings()
    reports: list[M1CoverageReport] = []
    for ticker in tickers:
        df = load_ohlcv(ticker, Timeframe.M1, settings=s)
        if df is None or df.empty:
            reports.append(M1CoverageReport(
                ticker=ticker.upper(),
                exists=False,
                bar_count=0,
                first_at=None,
                last_at=None,
                expected_bars=0,
                coverage_pct=0.0,
            ))
            continue
        count = int(len(df))
        first = Timestamp(df.index[0]).to_pydatetime()
        last = Timestamp(df.index[-1]).to_pydatetime()
        expected = max(1, int((last - first).total_seconds() // 60) + 1)
        reports.append(M1CoverageReport(
            ticker=ticker.upper(),
            exists=True,
            bar_count=count,
            first_at=first,
            last_at=last,
            expected_bars=expected,
            coverage_pct=round(count / expected * 100.0, 2),
        ))
    return tuple(reports)
