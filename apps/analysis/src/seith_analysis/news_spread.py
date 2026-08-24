"""Model biaya spread melebar khusus window news (E2 - WAJIB).

Saat rilis ekonomi besar, spread maker melebar drastis dalam hitungan detik;
backtest yang memakai spread normal akan mengklaim profit fiktif. Model ini
tier-based sederhana namun eksplisit dan bisa diganti empiris nanti begitu
pattern library punya data spread aktual.

Semua fungsi pure - tidak ada IO, mudah dites & dipakai backtest news (Gate Akhir).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SpreadTier:
    """Window relatif thd rilis (menit) dan pengali spread saat itu."""

    before_min: int
    after_min: int
    multiplier: float


#: Tier diurutkan dari yang paling parah; evaluator ambil tier pertama yang cocok.
#: Angka konservatif utk major FX/crypto likuid saat rilis high-importance;
#: revisi empiris menyusul dari data aktual (jangan dianggap final).
NEWS_SPREAD_TIERS: tuple[SpreadTier, ...] = (
    SpreadTier(before_min=2, after_min=10, multiplier=8.0),
    SpreadTier(before_min=5, after_min=30, multiplier=3.0),
)

DEFAULT_MULTIPLIER = 1.0


def widened_spread_multiplier(seconds_from_release: float) -> float:
    """Pengali spread pada offset detik tertentu dari waktu rilis.

    Negatif = sebelum rilis, positif = sesudah. Di luar semua tier = 1.0.
    Offset non-finite (nan/inf) ditolak keras - tidak ada fallback senyap.
    """
    if not math.isfinite(seconds_from_release):
        raise ValueError("seconds_from_release wajib finite")
    offset_min = seconds_from_release / 60.0
    for tier in NEWS_SPREAD_TIERS:
        if -tier.before_min <= offset_min <= tier.after_min:
            return tier.multiplier
    return DEFAULT_MULTIPLIER


def effective_spread_bps(base_spread_bps: float, seconds_from_release: float) -> float:
    """Spread efektif (bps) dengan widening window news."""
    if base_spread_bps < 0:
        raise ValueError("base_spread_bps wajib non-negatif")
    return base_spread_bps * widened_spread_multiplier(seconds_from_release)


def round_trip_cost_bps(base_spread_bps: float, entry_seconds_from_release: float,
                        exit_seconds_from_release: float) -> float:
    """Biaya bolak-balik (entry + exit) dalam bps utk satu trade window news."""
    return (
        effective_spread_bps(base_spread_bps, entry_seconds_from_release)
        + effective_spread_bps(base_spread_bps, exit_seconds_from_release)
    )
