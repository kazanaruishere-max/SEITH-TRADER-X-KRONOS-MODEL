"""Tabel durasi Timeframe kanonik - SATU sumber kebenaran untuk seluruh seith-data."""

from __future__ import annotations

from seith_core.schemas import Timeframe

TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.H1: 3600,
    Timeframe.H4: 14_400,
    Timeframe.D1: 86_400,
}


def timeframe_seconds(tf: Timeframe) -> int:
    return TIMEFRAME_SECONDS[tf]
