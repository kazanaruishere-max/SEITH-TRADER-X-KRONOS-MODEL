"""Intent router NL sederhana untuk /ask (Fase B2).

Memetakan pertanyaan bebas ke intent sebelum jatuh ke LLM generik.
Intent: FORECAST | POSITIONS | PNL | CALENDAR | GENERAL.
Ticker diekstrak bila ada (divalidasi via schema Ticker).
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import TypeAdapter
from seith_core.schemas import Ticker

_INTENT = StrEnum(
    "_INTENT",
    ["FORECAST", "POSITIONS", "PNL", "CALENDAR", "GENERAL"],
)

_TICKER_RE = re.compile(
    r"\b([A-Z]{2,10})(USDT|USD|PERP|BUSD)?\b"
)
_validate_ticker = TypeAdapter(Ticker)

_KEYWORDS = {
    _INTENT.POSITIONS: ("posisi", "position", "portfolio", "eksposur", "exposure", "hold"),
    _INTENT.PNL: ("pnl", "profit", "rugi", "untung", "loss", "balance", "saldo"),
    _INTENT.CALENDAR: (
        "kalender", "event", "rilis", "news ekonomi", "jadwal", "cpi", "fomc", "neraca"
    ),
    _INTENT.FORECAST: (
        "prediksi", "ramal", "forecast", "ke depan", "nantinya", "akan",
        "target harga", "harga ke", "naik", "turun", "kemana",
    ),
}


def extract_ticker(text: str) -> str | None:
    """Cari token yang lolos validasi Ticker; preferensi bersuffix USDT/USD."""
    hits: list[str] = []
    for m in _TICKER_RE.finditer(text):
        cand = m.group(1) + (m.group(2) or "")
        try:
            hits.append(_validate_ticker.validate_python(cand))
        except Exception:  # noqa: BLE001 - token biasa (kata kapital) ditolak
            continue
    if not hits:
        return None
    for h in hits:  # prefer yang sudah punya suffix market
        if h.endswith(("USDT", "USD", "PERP")):
            return h
    return hits[0]


def classify_intent(text: str) -> tuple[str, str | None]:
    """Return (intent, ticker|None)."""
    low = text.lower()
    for intent, kws in _KEYWORDS.items():
        if any(kw in low for kw in kws):
            ticker = extract_ticker(text)
            if intent is _INTENT.FORECAST and ticker is None:
                # keyword masa depan tapi tak ada ticker -> tetap general
                return _INTENT.GENERAL.value, None
            return intent.value, ticker
    return _INTENT.GENERAL.value, extract_ticker(text)
