"""Binance spot klines via ccxt (public, tanpa API key)."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import ccxt
import pandas as pd
from seith_core.schemas import Timeframe

from seith_data.timeutil import timeframe_seconds

logger = logging.getLogger(__name__)

QUOTES = ("USDT", "FDUSD", "USDC", "TUSD", "BUSD", "USD", "BTC", "ETH")
_PAGE_LIMIT = 1000
_RETRY_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 2.0


def to_ccxt_symbol(ticker: str) -> str:
    t = ticker.upper()
    for quote in QUOTES:
        if t.endswith(quote) and len(t) > len(quote):
            return f"{t[: -len(quote)]}/{quote}"
    raise ValueError(f"ticker '{ticker}' tidak dikenali sebagai pasangan Binance")


def _require_aware(ts: datetime, name: str) -> datetime:
    if ts.tzinfo is None:
        raise ValueError(f"'{name}' wajib timezone-aware (UTC), dapat datetime naive")
    return ts.astimezone(UTC)


def _fetch_page_retry(exchange, symbol: str, tf_value: str, since_ms: int) -> list[list]:
    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return exchange.fetch_ohlcv(symbol, tf_value, since=since_ms, limit=_PAGE_LIMIT)
        except (ccxt.NetworkError, ccxt.RateLimitExceeded) as exc:
            last_exc = exc
            wait = _RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "ccxt %s (attempt %d/%d), retry dalam %.0fs", exc, attempt, _RETRY_ATTEMPTS, wait
            )
            time.sleep(wait)
    raise RuntimeError(f"ccxt gagal setelah {_RETRY_ATTEMPTS} percobaan") from last_exc


def fetch(
    ticker: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    start = _require_aware(start, "start")
    end = _require_aware(end, "end")
    exchange = ccxt.binance({"enableRateLimit": True})
    symbol = to_ccxt_symbol(ticker)
    since = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    step_ms = timeframe_seconds(timeframe) * 1000
    rows: list[list] = []
    while since < end_ms:
        batch = _fetch_page_retry(exchange, symbol, timeframe.value, since)
        if not batch:
            break
        rows.extend(batch)
        last_ts = batch[-1][0]
        next_since = last_ts + step_ms
        if next_since <= since:
            break  # guard anti infinite-loop
        since = next_since
        if len(batch) < _PAGE_LIMIT:
            break
    if not rows:
        raise RuntimeError(f"tidak ada data Binance untuk {symbol} {timeframe}")
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").astype("float64")
    df = df[~df.index.duplicated(keep="last")]
    # buang candle yang masih terbentuk (close belum final) agar store hanya berisi bar matang
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    mature_cut = pd.Timestamp(now_ms - step_ms, unit="ms", tz="UTC")
    return df[df.index <= mature_cut]
