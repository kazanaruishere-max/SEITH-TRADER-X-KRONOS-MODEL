"""OANDA v20 candles (practice/live sesuai settings)."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import pandas as pd
import requests
from seith_core.config import get_settings
from seith_core.schemas import Timeframe

from seith_data.timeutil import timeframe_seconds

logger = logging.getLogger(__name__)

_GRANULARITY = {
    Timeframe.M1: "M1",
    Timeframe.M5: "M5",
    Timeframe.M15: "M15",
    Timeframe.H1: "H1",
    Timeframe.H4: "H4",
    Timeframe.D1: "D",
}
_PAGE_SIZE = 5000
_RETRY_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 2.0


def _require_aware(ts: datetime, name: str) -> datetime:
    if ts.tzinfo is None:
        raise ValueError(f"'{name}' wajib timezone-aware (UTC), dapat datetime naive")
    return ts.astimezone(UTC)


def _get_retry(url: str, headers: dict, params: dict) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            transient = resp.status_code >= 500 or resp.status_code == 429
            if not transient:
                return resp
            last_exc = RuntimeError(f"OANDA HTTP {resp.status_code}")
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
        wait = _RETRY_BASE_SECONDS * (2 ** (attempt - 1))
        logger.warning("OANDA retry %d/%d dalam %.0fs", attempt, _RETRY_ATTEMPTS, wait)
        time.sleep(wait)
    raise RuntimeError(f"OANDA gagal setelah {_RETRY_ATTEMPTS} percobaan") from last_exc


def fetch(
    ticker: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    s = get_settings()
    if not s.oanda.access_token:
        raise RuntimeError("OANDA access token belum dikonfigurasi")
    start = _require_aware(start, "start")
    end = _require_aware(end, "end")
    instrument = ticker.upper()
    granularity = _GRANULARITY[timeframe]
    headers = {"Authorization": f"Bearer {s.oanda.access_token.get_secret_value()}"}
    url = f"{s.oanda.base_url}/v3/instruments/{instrument}/candles"
    cursor = start
    frames: list[pd.DataFrame] = []
    while cursor < end:
        # OANDA menolak kombinasi count+to dalam satu request -> paginasi
        # hanya dari 'cursor' dengan count, berhenti saat data habis/melewati end.
        params = {
            "granularity": granularity,
            "price": "M",
            "count": _PAGE_SIZE,
            "from": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        resp = _get_retry(url, headers, params)
        resp.raise_for_status()
        payload = resp.json()
        candles = [
            c for c in payload.get("candles", [])
            if c["complete"] and pd.Timestamp(c["time"]) <= pd.Timestamp(end)
        ]
        if not candles:
            break
        rows = [
            {
                "timestamp": pd.Timestamp(c["time"]).tz_convert("UTC"),
                **{k: float(c["mid"][k[0]]) for k in ("open", "high", "low", "close")},
                "volume": float(c["volume"]),
            }
            for c in candles
        ]
        frames.append(pd.DataFrame(rows).set_index("timestamp"))
        last_time = pd.Timestamp(candles[-1]["time"]).tz_convert("UTC")
        step = pd.Timedelta(seconds=timeframe_seconds(timeframe))
        next_cursor = (last_time + step).to_pydatetime()
        if next_cursor <= cursor:
            break  # guard anti infinite-loop
        cursor = next_cursor
    if not frames:
        raise RuntimeError(f"tidak ada data OANDA untuk {instrument} {timeframe}")
    df = pd.concat(frames).astype("float64")
    return df[~df.index.duplicated(keep="last")]
