"""Source modules: binance (ccxt), oanda (v20 REST), yfinance."""

from seith_data.sources import binance, oanda, yf
from seith_data.sources.binance import QUOTES


def detect_source(ticker: str) -> str:
    """Auto-deteksi source dari bentuk ticker.

    - mengandung '_' (EUR_USD) -> oanda
    - diakhiri quote crypto umum (BTCUSDT) -> binance
    - sisanya diasumsikan saham US -> yfinance
    """
    t = ticker.upper()
    if "_" in t:
        return "oanda"
    if any(t.endswith(q) and len(t) > len(q) for q in QUOTES):
        return "binance"
    return "yfinance"


__all__ = ["QUOTES", "binance", "detect_source", "oanda", "yf"]
