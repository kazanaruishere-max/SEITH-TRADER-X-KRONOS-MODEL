"""Kalender ekonomi: Finnhub (utama) + ForexFactory weekly JSON (fallback).

Mapping negara -> ticker terdampak eksplisit di CURRENCY_IMPACT_MAP; rilis
dari mata uang yang tidak dipetakan dilewati dengan log debug (bukan error)
agar provider baru tidak merusak pipeline.

Nilai numerik ForexFactory berformat teks ("185K", "2.4%") - dinormalisasi
_parse_ff_number ke float murni atau None.
"""

from __future__ import annotations

import logging
import math
import re
import time
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from pydantic import ValidationError
from seith_core.config import get_settings
from seith_core.schemas import AssetClass, EconomicEvent, EventImportance

logger = logging.getLogger(__name__)

FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"
FOREXFACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

_RETRY_ATTEMPTS = 3
_RETRY_BASE_SECONDS = 2.0

#: Kode mata uang -> tuple pasangan SEITH yang paling terdampak rilisnya.
#: Crypto majors ikut merasakan rilis makro US (CPI/NFP menggerakkan risk-on/off)
#: - sesuai vision owner; dipisah dari FOREX agar bisa difilter di konsumsi.
CURRENCY_IMPACT_MAP: dict[str, tuple[tuple[str, AssetClass], ...]] = {
    "USD": (
        ("EUR_USD", AssetClass.FOREX),
        ("BTCUSDT", AssetClass.CRYPTO),
        ("ETHUSDT", AssetClass.CRYPTO),
        ("AAPL", AssetClass.EQUITY_US),
        ("NVDA", AssetClass.EQUITY_US),
    ),
    "EUR": (("EUR_USD", AssetClass.FOREX),),
}

#: Normalisasi penanda country/currency provider -> kode mata uang ISO.
_COUNTRY_TO_CURRENCY: dict[str, str] = {
    "US": "USD",
    "USA": "USD",
    "UNITED STATES": "USD",
    "EU": "EUR",
    "EZ": "EUR",
    "DE": "EUR",
    "FR": "EUR",
    "IT": "EUR",
    "ES": "EUR",
    "NL": "EUR",
    "EURO AREA": "EUR",
}

_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")

_FF_SUFFIX_SCALE = {"K": 1_000.0, "M": 1_000_000.0, "B": 1_000_000_000.0}


class CalendarSourceError(RuntimeError):
    """Gagal mengambil kalender dari provider setelah retry."""


def get_json_retry(
    session: requests.Session,
    url: str,
    params: dict[str, str] | None = None,
) -> Any:  # noqa: ANN401 - payload JSON bebas dari provider
    """GET JSON dengan retry eksponensial; dipakai semua news/calendar source.

    Pesan log TIDAK PERNAH memuat `exc` mentah: pesan exception requests
    menyematkan URL lengkap termasuk query token (kebocoran secret).
    """
    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            resp = session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # 4xx selain 429 = non-transien (auth/route salah) - jangan dibuang2 waktu
            transient = status is None or status >= 500 or status == 429
            if not transient:
                break
            if attempt == _RETRY_ATTEMPTS:
                break
            wait = _RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning("HTTP gagal (%s, status=%s) attempt %d/%d, retry %.0fs",
                           type(exc).__name__, status, attempt, _RETRY_ATTEMPTS, wait)
            time.sleep(wait)
    raise CalendarSourceError(f"fetch gagal setelah {_RETRY_ATTEMPTS} percobaan") from last_exc


def slugify_event_type(raw: str) -> str:
    """'Non-Farm Payrolls' -> 'non_farm_payrolls'; aman utk dedup natural key."""
    return _SLUG_CLEAN.sub("_", raw.strip().lower()).strip("_")


def canonical_event_type(slug: str) -> str:
    """Samakan kosakata event lintas provider (FF/Finnhub/FRED -> satu bucket).

    Implementasi kanonik ada di sources.fred_calendar; fungsi ini delegasi agar
    kedua jalur (live & historis) PASTI memakai tabel aturan yang sama.
    """
    from seith_data.sources.fred_calendar import canonical_event_type as _canonical

    return _canonical(slug)


def to_currency_code(country: str) -> str | None:
    """Normalisasi label country provider ('US','Euro Area') -> kode mata uang."""
    return _COUNTRY_TO_CURRENCY.get(country.strip().upper())


def parse_number(raw: object) -> float | None:
    """Parse angka provider ('185K', '2.4%', '-0.5') -> float; None bila kosong.

    NaN/Infinity dari provider DITOLAK (None) - nilai non-finite meracuni
    agregasi pattern library dan mematahkan round-trip JSON.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if math.isfinite(value) else None
    text = str(raw).strip().rstrip("%").strip()
    if not text:
        return None
    scale = _FF_SUFFIX_SCALE.get(text[-1].upper())
    try:
        if scale is not None and len(text) > 1:
            value = float(Decimal(text[:-1].strip()) * Decimal(str(scale)))
        else:
            value = float(Decimal(text.replace(",", "")))
    except InvalidOperation:
        return None
    return value if math.isfinite(value) else None


def map_importance(raw: object) -> EventImportance | None:
    """Peta label impact provider -> enum; None bila tak dikenal (event dilewati).

    Fallback ke MEDIUM untuk label asing = misklasifikasi senyap yang mencemari
    bucket trigger E3, jadi sengaja TIDAK ada fallback di sini.
    """
    try:
        return EventImportance(str(raw).strip().lower())
    except ValueError:
        logger.debug("impact tidak dikenal dilewati: %r", raw)
        return None


def _build_event_safe(**kwargs: Any) -> EconomicEvent | None:
    """Konstruksi event per-baris: baris rusak DILEWATI, bukan menggugurkan batch."""
    try:
        return EconomicEvent(**kwargs)
    except ValidationError as exc:
        logger.debug("baris kalender tidak valid dilewati (%s)", exc.error_count())
        return None


def fetch_finnhub_calendar(
    start: date,
    end: date,
    api_key: str | None = None,
    session: requests.Session | None = None,
) -> list[EconomicEvent]:
    """Rilis ekonomi [start, end] dari Finnhub; waktu provider diasumsikan UTC.

    Asumsi naive=UTC perlu sanity-check manual terhadap rilis publik yang jamnya
    diketahui (mis. NFP 08:30 ET) sebelum dipakai forward-test.
    """
    token = api_key
    if not token:
        key = get_settings().finnhub.api_key
        token = key.get_secret_value() if key is not None else None
    if not token:
        raise RuntimeError("Finnhub belum terkonfigurasi (SEITH_FINNHUB__API_KEY)")
    sess = session or requests.Session()
    payload = get_json_retry(sess, FINNHUB_URL, {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "token": token,
    })
    items = payload.get("economicCalendar", []) if isinstance(payload, dict) else []
    events: list[EconomicEvent] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_time = str(item.get("time", "")).strip()  # '2026-09-04 12:30:00'
        try:
            scheduled_at = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            logger.debug("baris kalender tanpa waktu valid dilewati: %r", raw_time[:32])
            continue
        currency = to_currency_code(str(item.get("country", "")))
        if currency is None:
            continue
        importance = map_importance(item.get("impact"))
        event_type = canonical_event_type(slugify_event_type(str(item.get("event", ""))))
        if importance is None or not event_type:
            continue
        ref = f"finnhub:{event_type}:{scheduled_at.isoformat()}"
        for ticker, asset_class in CURRENCY_IMPACT_MAP.get(currency, ()):
            event = _build_event_safe(
                source_ref=ref,
                source="finnhub",
                ticker=ticker,
                asset_class=asset_class,
                event_type=event_type,
                importance=importance,
                currency=currency,
                scheduled_at=scheduled_at,
                actual=parse_number(item.get("actual")),
                forecast=parse_number(item.get("estimate")),
                previous=parse_number(item.get("prev")),
            )
            if event is not None:
                events.append(event)
    return events


def fetch_forexfactory_week(
    session: requests.Session | None = None,
) -> list[EconomicEvent]:
    """Kalender minggu berjalan ForexFactory (fallback gratis, tanpa API key)."""
    sess = session or requests.Session()
    items = get_json_retry(sess, FOREXFACTORY_URL)
    if not isinstance(items, list):
        raise CalendarSourceError("format ForexFactory tidak terduga")
    events: list[EconomicEvent] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_date = str(item.get("date", "")).strip()
        try:
            scheduled_at = datetime.fromisoformat(raw_date)
        except ValueError:
            logger.debug("baris FF tanpa tanggal valid dilewati: %r", raw_date[:32])
            continue
        # FF kadang mengirim offset; bila naive JANGAN pakai tz lokal mesin
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=UTC)
        scheduled_at = scheduled_at.astimezone(UTC)
        code = str(item.get("country", "")).strip().upper()
        currency = to_currency_code(code) or (
            code if re.fullmatch(r"[A-Z]{3}", code) else None
        )
        if currency is None:
            logger.debug("FF country '%s' tidak dikenali - dilewati",
                         item.get("country"))
            continue
        importance = map_importance(item.get("impact"))
        event_type = canonical_event_type(slugify_event_type(str(item.get("title", ""))))
        if importance is None or not event_type:
            continue
        ref = f"forexfactory:{event_type}:{scheduled_at.isoformat()}"
        for ticker, asset_class in CURRENCY_IMPACT_MAP.get(currency, ()):
            event = _build_event_safe(
                source_ref=ref,
                source="forexfactory",
                ticker=ticker,
                asset_class=asset_class,
                event_type=event_type,
                importance=importance,
                currency=currency,
                scheduled_at=scheduled_at,
                actual=parse_number(item.get("actual")),
                forecast=parse_number(item.get("forecast")),
                previous=parse_number(item.get("previous")),
            )
            if event is not None:
                events.append(event)
    return events
