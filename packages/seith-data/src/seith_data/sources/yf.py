"""yfinance OHLCV untuk saham US.

Catatan batasan yfinance (berlaku upstream, bukan bug kita):
- interval 1m hanya ~7 hari terakhir; 60m ~730 hari
- interval '4h' tidak didukung -> gunakan resample dari 1h bila perlu
"""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd
import yfinance as yf
from seith_core.schemas import Timeframe

logger = logging.getLogger(__name__)

_INTERVAL = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.H1: "60m",
    Timeframe.D1: "1d",
}


def fetch(
    ticker: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime | None = None,
) -> pd.DataFrame:
    if timeframe not in _INTERVAL:
        raise NotImplementedError(f"yfinance tidak mendukung timeframe {timeframe}")
    if start.tzinfo is None:
        raise ValueError("'start' wajib timezone-aware (UTC), dapat datetime naive")
    # CATATAN [BLOCKER-fix]: parameter 'end' SENGAJA tidak dikirim - yfinance
    # men-truncate end ke tengah malam dan bersifat eksklusif, sehingga
    # mengirim 'end' membuang senyap seluruh bar hari terakhir.
    raw = yf.download(
        ticker,
        start=start.date().isoformat(),
        interval=_INTERVAL[timeframe],
        auto_adjust=False,
        progress=False,
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"tidak ada data yfinance untuk {ticker} {timeframe}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = raw.rename(columns=str.lower)[list(("open", "high", "low", "close", "volume"))]
    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    df.index = idx
    df.index.name = "timestamp"
    if end is not None:
        if end.tzinfo is None:
            raise ValueError("'end' wajib timezone-aware (UTC), dapat datetime naive")
        df = df[df.index <= pd.Timestamp(end)]
    return df.astype("float64").sort_index()[~df.sort_index().index.duplicated(keep="last")]
