"""GATE-A: Backtest walk-forward strategi news-driven.

DESAIN WALK-FORWARD (anti in-sample, ditegakkan by-construction + test):
- Event historis dipecah per bulan kalender.
- `seed_months` bulan pertama = pembentukan library saja (tanpa trading).
- Tiap bulan uji berikutnya: pattern library dibangun EKSKLUSIF dari event
  dengan scheduled_at SEBELUM awal bulan uji (expanding window), lalu event
  DALAM bulan uji disimulasikan memakai library tersebut.
- Konsekuensi: keputusan arah tiap trade TIDAK PERNAH melihat masa depan -
  persis kondisi trigger produksi E3 yang hanya boleh tahu pola masa lalu.

MODEL BIAYA: spread melebar window news (news_spread.round_trip_cost_bps):
entry ~60 detik pasca-rilis (tier terburuk 8x), exit pada offset horizon.

SIZING: unit exposure - hasil dalam % return per satuan notional. Sizing riil
adalah wewenang RiskManager saat intake; angka ini untuk evaluasi strategi.

BATASAN JUJUR: gate surprise-factor trigger LIVE tidak dapat direplikasi di
historis (event FRED actual=None); Gate-A menguji EDGE POLA continuation,
bukan edge surprise. Bucket KUAT hasilnya adalah kandidat, bukan janji.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pandas as pd
from seith_core.schemas import EconomicEvent, EventImportance

from seith_analysis.news_spread import round_trip_cost_bps

logger = logging.getLogger("seith.backtest_news")

M1Loader = Callable[[str], pd.DataFrame | None]

#: Spread dasar pasar wajar likuid (bps); revisi empiris menyusul.
BASE_SPREAD_BPS = {"EUR_USD": 1.0, "BTCUSDT": 2.0, "ETHUSDT": 2.5}

_ENTRY_OFFSET_SECONDS = 60  # entry di close bar rilis (~1 menit pasca mulai rilis)


@dataclass(frozen=True)
class NewsTradeResult:
    """Satu trade simulasi satu rilis pada satu horizon."""

    ticker: str
    event_type: str
    horizon_minutes: int
    released_at: datetime
    direction: int  # +1 long / -1 short
    entry_price: float
    exit_price: float
    gross_pct: float
    cost_pct: float

    @property
    def net_pct(self) -> float:
        return self.gross_pct - self.cost_pct

    @property
    def is_win(self) -> bool:
        return self.net_pct > 0


@dataclass
class BucketStat:
    """Agregasi performa satu bucket (ticker x event_type x horizon)."""

    ticker: str
    event_type: str
    horizon_minutes: int
    n_trades: int = 0
    wins: int = 0
    total_net_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    gross_wins_pct: float = 0.0
    gross_losses_pct: float = 0.0
    _nets: list[float] = field(default_factory=list)

    def add(self, t: NewsTradeResult) -> None:
        self.n_trades += 1
        self.total_net_pct += t.net_pct
        self._nets.append(t.net_pct)
        if t.gross_pct > 0:
            self.gross_wins_pct += t.gross_pct
        elif t.gross_pct < 0:
            self.gross_losses_pct += abs(t.gross_pct)
        if t.is_win:
            self.wins += 1

    def finalize(self) -> None:
        """Hitung drawdown dari kurva kumulatif net (panggil di akhir agregasi)."""
        peak = cum = worst = 0.0
        for v in self._nets:
            cum += v
            peak = max(peak, cum)
            worst = min(worst, cum - peak)
        self.max_drawdown_pct = abs(worst)

    @property
    def win_rate(self) -> float:
        return self.wins / self.n_trades if self.n_trades else 0.0

    @property
    def profit_factor(self) -> float | None:
        if self.gross_losses_pct == 0:
            return None if self.gross_wins_pct == 0 else float("inf")
        return self.gross_wins_pct / self.gross_losses_pct


@dataclass
class WalkForwardReport:
    """Hasil lengkap Gate-A + jejak integritas walk-forward."""

    stats: list[BucketStat] = field(default_factory=list)
    n_months_tested: int = 0
    n_trades_total: int = 0
    skipped_no_grid: int = 0
    skipped_no_pattern_or_gate: int = 0
    train_cutoffs: list[date] = field(default_factory=list)


def _month_starts(first: date, last: date) -> list[date]:
    starts: list[date] = []
    y, m = first.year, first.month
    while date(y, m, 1) <= last:
        starts.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return starts


def _next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def simulate_trade(
    m1: pd.DataFrame,
    release_at: datetime,
    horizon_minutes: int,
    direction: int,
    ticker: str,
    event_type: str,
) -> NewsTradeResult | None:
    """Simulasi satu trade: entry close bar rilis, exit H menit kemudian.

    Return None bila grid tidak kontinyu / bar rilis tidak tersedia -
    TIDAK PERNAH menghalalkan window parsial (konsisten pattern_library).
    """
    release_at = release_at.astimezone(UTC)
    if not isinstance(m1.index, pd.DatetimeIndex) or m1.index.tz is None:
        raise ValueError("m1 index wajib DatetimeIndex tz-aware")
    idx = m1.index.tz_convert(UTC)
    if not idx.is_monotonic_increasing:
        raise ValueError("m1 index wajib monotonik naik")

    entry_pos = int(idx.searchsorted(pd.Timestamp(release_at), side="left"))
    exit_pos = entry_pos + horizon_minutes
    if exit_pos >= len(idx):
        return None
    if idx[entry_pos] > release_at + pd.Timedelta(minutes=1):
        return None  # bar rilis bolong - jangan pakai bar lain sebagai pengganti
    if idx[exit_pos] - idx[entry_pos] != pd.Timedelta(minutes=horizon_minutes):
        return None  # grid tidak kontinu

    entry = float(m1.iloc[entry_pos]["close"])
    exit_px = float(m1.iloc[exit_pos]["close"])
    if entry <= 0:
        return None
    gross = (exit_px - entry) / entry * 100.0 * direction
    cost_bps = round_trip_cost_bps(
        BASE_SPREAD_BPS.get(ticker, 2.0),
        entry_seconds_from_release=_ENTRY_OFFSET_SECONDS,
        exit_seconds_from_release=horizon_minutes * 60,
    )
    return NewsTradeResult(
        ticker=ticker.upper(),
        event_type=event_type,
        horizon_minutes=horizon_minutes,
        released_at=release_at,
        direction=direction,
        entry_price=round(entry, 8),
        exit_price=round(exit_px, 8),
        gross_pct=round(gross, 6),
        cost_pct=round(cost_bps / 100.0, 6),
    )


def run_walk_forward(
    events: list[EconomicEvent],
    m1_loader: M1Loader,
    *,
    horizons: tuple[int, ...] = (15,),
    seed_months: int = 4,
    min_samples: int = 10,
    min_continuation_prob: float = 0.55,
    min_importance: EventImportance = EventImportance.MEDIUM,
) -> WalkForwardReport:
    """Driver walk-forward penuh. Lihat docstring modul untuk desain."""
    from seith_analysis.pattern_library import build_pattern_library

    ranked = {EventImportance.LOW: 1, EventImportance.MEDIUM: 2, EventImportance.HIGH: 3}
    ordered = sorted(events, key=lambda e: e.scheduled_at)
    if len(ordered) < 2:
        raise ValueError("event terlalu sedikit untuk walk-forward")
    months = _month_starts(ordered[0].scheduled_at.date(), ordered[-1].scheduled_at.date())
    if len(months) <= seed_months:
        raise ValueError(f"bulan uji tidak ada: hanya {len(months)} bulan, seed {seed_months}")

    cache: dict[str, pd.DataFrame | None] = {}

    def loader_cached(ticker: str):
        if ticker not in cache:
            df = m1_loader(ticker)
            cache[ticker] = df.sort_index() if df is not None and not df.empty else None
        return cache[ticker]

    report = WalkForwardReport()
    buckets: dict[tuple[str, str, int], BucketStat] = {}

    for i in range(seed_months, len(months)):
        cutoff = months[i]
        month_end = _next_month(cutoff)
        train = [e for e in ordered if e.scheduled_at.date() < cutoff]
        test = [
            e for e in ordered if cutoff <= e.scheduled_at.date() < month_end
        ]
        if not train or not test:
            continue
        library = build_pattern_library(train, loader_cached, horizons=horizons)
        report.n_months_tested += 1
        report.train_cutoffs.append(cutoff)

        by_key = {(p.ticker, p.event_type, p.horizon_minutes): p for p in library}
        for ev in test:
            df = loader_cached(ev.ticker)
            if df is None:
                continue
            for h in horizons:
                pat = by_key.get((ev.ticker.upper(), ev.event_type, h))
                gated = (
                    pat is not None
                    and pat.sample_count >= min_samples
                    and pat.prob_continuation >= min_continuation_prob
                    and ranked[ev.importance] >= ranked[min_importance]
                    and pat.prob_initial_up != 0.5
                )
                if not gated:
                    report.skipped_no_pattern_or_gate += 1
                    continue
                direction = 1 if pat.prob_initial_up > 0.5 else -1
                trade = simulate_trade(df, ev.scheduled_at, h, direction,
                                       ev.ticker, ev.event_type)
                if trade is None:
                    report.skipped_no_grid += 1
                    continue
                key = (trade.ticker, trade.event_type, h)
                stat = buckets.setdefault(key, BucketStat(*key))
                stat.add(trade)
                report.n_trades_total += 1

    for stat in buckets.values():
        stat.finalize()
    report.stats = sorted(
        buckets.values(),
        key=lambda s: (-s.total_net_pct, s.ticker, s.event_type),
    )
    return report
