"""Kalender ekonomi historis GRATIS via FRED release-dates (ADR-0003).

Insight desain: pattern library historis hanya butuh TIMESTAMP rilis + harga m1 -
kolom forecast/surprise TIDAK diisi utk data lama (surprise cuma wajib di jalur
LIVE E3 yang memakai ForexFactory/Finnhub).

Jam rilis dari aturan jadwal agensi (umumnya 08:30 America/New_York),
dikonversi DST-aware ke UTC. Release ID diverifikasi empiris terhadap API
/releases pada 2026-08-24 (jangan tebak-nebak ID).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
from seith_core.config import get_settings
from seith_core.schemas import EconomicEvent, EventImportance

from seith_data.sources.economic_calendar import (
    CURRENCY_IMPACT_MAP,
    _build_event_safe,
    get_json_retry,
)

logger = logging.getLogger(__name__)

FRED_RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"
NY_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class FredReleaseSpec:
    """Satu rilis agensi US: ID FRED + kanonik event_type + jam lokal NY."""

    release_id: int
    event_type: str  # SUDAH kanonik (lihat CANONICAL_EVENT_RULES)
    importance: EventImportance
    local_hhmm: str = "08:30"


#: MVP US-only (ADR-0003): frekuensi tertinggi dulu agar pattern library cepat
#: punya sampel; FOMC menyusul iterasi 2.
FRED_RELEASE_SPECS: tuple[FredReleaseSpec, ...] = (
    FredReleaseSpec(180, "jobless_claims", EventImportance.MEDIUM),      # mingguan
    FredReleaseSpec(50, "nonfarm_payrolls", EventImportance.HIGH),       # bulanan
    FredReleaseSpec(10, "cpi", EventImportance.HIGH),                    # bulanan
    FredReleaseSpec(54, "pce", EventImportance.HIGH),                    # bulanan
    FredReleaseSpec(9, "retail_sales", EventImportance.MEDIUM),          # bulanan
    FredReleaseSpec(53, "gdp", EventImportance.HIGH),                    # kuartalan
)


def canonical_event_type(slug: str) -> str:
    """Samakan kosakata event lintas provider (FF/Finnhub/FRED -> satu bucket).

    Semua varian cut yang keluar pada momen yang sama digabung satu bucket
    (cpi m/m, cpi y/y, core cpi -> 'cpi') karena pola harga reaksinya identik.
    """
    s = slug.strip().lower()
    rules: tuple[tuple[str, str], ...] = (
        (r"^non[-_]?farm", "nonfarm_payrolls"),
        (r"^employment_situation$", "nonfarm_payrolls"),
        (r"^initial|^jobless", "jobless_claims"),
        (r"^core_cpi", "cpi"),
        (r"^cpi|^consumer_price", "cpi"),
        (r"^core_pce|^pce", "pce"),
        (r"(^|[-_])gdp", "gdp"),
        (r"^retail", "retail_sales"),
        (r"^unemployment_rate$", "unemployment_rate"),
    )
    for pattern, canonical in rules:
        # search (bukan match) agar rule infix seperti (^|[_-])gdp bekerja
        if re.search(pattern, s):
            return canonical
    return s


def _parse_local_hhmm(raw: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{2}):(\d{2})", raw.strip())
    if not match:
        raise ValueError(f"local_hhmm tidak valid: {raw!r}")
    return int(match.group(1)), int(match.group(2))


def fetch_fred_release_dates(
    spec: FredReleaseSpec,
    start: date,
    end: date,
    api_key: str | None = None,
    session: requests.Session | None = None,
) -> list[date]:
    """Tanggal publikasi aktual satu rilis dalam rentang [start, end]."""
    token = api_key
    if not token:
        secret = get_settings().fred.api_key
        token = secret.get_secret_value() if secret is not None else None
    if not token:
        raise RuntimeError("FRED belum terkonfigurasi (SEITH_FRED__API_KEY)")
    sess = session or requests.Session()
    payload: Any = get_json_retry(sess, FRED_RELEASE_DATES_URL, {
        "release_id": str(spec.release_id),
        "api_key": token,
        "file_type": "json",
        "sort_order": "desc",
        "limit": "1000",
    })
    entries = payload.get("release_dates", []) if isinstance(payload, dict) else []
    dates: list[date] = []
    for entry in entries:
        raw = str(entry.get("date", "")).strip() if isinstance(entry, dict) else ""
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            continue
        if start <= d <= end:
            dates.append(d)
    return sorted(set(dates))


def fetch_fred_calendar(
    start: date,
    end: date,
    api_key: str | None = None,
    session: requests.Session | None = None,
    specs: tuple[FredReleaseSpec, ...] | None = None,
) -> list[EconomicEvent]:
    """Konstruksi EconomicEvent historis dari tanggal publikasi FRED."""
    active_specs = specs or FRED_RELEASE_SPECS
    targets = CURRENCY_IMPACT_MAP.get("USD", ())
    events: list[EconomicEvent] = []
    for spec in active_specs:
        hour, minute = _parse_local_hhmm(spec.local_hhmm)
        for d in fetch_fred_release_dates(spec, start, end, api_key=api_key, session=session):
            local_dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=NY_TZ)
            scheduled_at = local_dt.astimezone(ZoneInfo("UTC"))
            ref = f"fred:{spec.release_id}:{d.isoformat()}:{scheduled_at.isoformat()}"
            for ticker, asset_class in targets:
                event = _build_event_safe(
                    source_ref=ref,
                    source="fred",
                    ticker=ticker,
                    asset_class=asset_class,
                    event_type=canonical_event_type(spec.event_type),
                    importance=spec.importance,
                    currency="USD",
                    scheduled_at=scheduled_at,
                )
                if event is not None:
                    events.append(event)
    return events
