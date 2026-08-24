"""Pattern Library Builder (E2 OTAK - inti news-driven engine).

Untuk tiap rilis ekonomi: ukur jalur harga m1 pasca-rilis pada horizon
5m/15m/1h/4h -> spike size, arah impuls, %retracement, prob lanjut/balik.
Agregasi per (ticker x event_type x horizon) menjadi PatternSummary yang
dipakai trigger strategi E3.

INVARIANT ANTI-LOOKAHEAD (ditegakkan `measure_release_window`, diuji eksplisit):
1. KONVENSI BAR OPEN-TIME WAJIB: bar bertimestamp T mencakup [T, T+1menit).
   Semua source SEITH (Binance ccxt, OANDA v20) memakai label start-time.
   Bar rilis 12:30 berarti window mulai di bar 12:30 - harga PRA-rilis tidak
   mungkin masuk. Jika suatu feed ternyata close-time-labeled, JANGAN dipakai
   di sini sebelum dikonversi - akan terjadi bias lookahead senyap.
2. Harga referensi = close bar TERAKHIR yang timestamp-nya KETAT < waktu rilis.
3. Metrik hanya boleh memakai bar dalam window [rilis, rilis+horizon).
4. Horizon dengan data tidak lengkap DIBUANG seluruhnya - tidak ada window
   parsial yang masuk agregasi.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from seith_core.schemas import EconomicEvent, PatternSummary

logger = logging.getLogger("seith.pattern_library")

HORIZON_MINUTES: tuple[int, ...] = (5, 15, 60, 240)

#: Retracement <= threshold ini dianggap impuls BERTAHAN (prob lanjut).
CONTINUATION_RETRACEMENT_MAX_PCT = 50.0

M1Loader = Callable[[str], pd.DataFrame | None]


@dataclass(frozen=True)
class HorizonMetrics:
    """Pengukuran satu rilis pada satu horizon (artefak internal analysis)."""

    event_id: str
    ticker: str
    event_type: str
    release_at: datetime
    horizon_minutes: int
    reference_price: float
    spike_size_pct: float
    initial_direction: int  # +1 naik / -1 turun
    move_at_horizon_pct: float
    retracement_pct: float
    continued: bool
    is_reversed: bool


def _require_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("release_at wajib timezone-aware")
    return ts.astimezone(UTC)


def measure_release_window(
    m1: pd.DataFrame,
    release_at: datetime,
    event_id: str,
    ticker: str,
    event_type: str,
    horizons: tuple[int, ...] = HORIZON_MINUTES,
) -> tuple[HorizonMetrics, ...]:
    """Ukur metrik pasca-rilis pada tiap horizon yang datanya LENGKAP.

    Window horizon H hanya valid bila memuat persis H bar m1 kontinyu mulai
    dari bar rilis - window parsial dibuang tanpa kecuali.
    """
    release_at = _require_utc(release_at)
    if not isinstance(m1.index, pd.DatetimeIndex):
        raise ValueError("m1 index wajib DatetimeIndex")
    if m1.index.tz is None:
        raise ValueError("m1 index wajib tz-aware UTC")
    idx = m1.index.tz_convert(UTC)
    if not idx.is_monotonic_increasing:
        raise ValueError("m1 index wajib monotonik naik (sort dulu sebelum dipakai)")

    # posisi bar pertama yang timestamp-nya >= waktu rilis;
    # bar-bar KETAT sebelum itu adalah zona referensi (anti-lookahead rule 1)
    pos_release = int(idx.searchsorted(pd.Timestamp(release_at), side="left"))
    if pos_release < 1:
        logger.debug("rilis %s dilewati: tidak ada bar sebelum rilis", event_id)
        return ()
    ref_price = float(m1.iloc[pos_release - 1]["close"])
    if not ref_price > 0:
        logger.debug("rilis %s dilewati: harga referensi tidak valid", event_id)
        return ()

    results: list[HorizonMetrics] = []
    for minutes in sorted(horizons):
        end = release_at + pd.Timedelta(minutes=minutes)
        start_pos = pos_release  # bar pertama dalam window = bar bertimestamp rilis
        end_pos = int(idx.searchsorted(end, side="left"))
        window = m1.iloc[start_pos:end_pos]
        # anti-lookahead rules 3+4: wajib persis H bar DAN kontinu interior -
        # setiap bar berjarak tepat 1 menit (gap + duplikat yang saling
        # mengompensasi TIDAK boleh lolos lewat cek endpoint saja)
        if len(window) != minutes:
            continue
        expected_grid = pd.date_range(
            idx[start_pos], periods=minutes, freq="1min", tz=idx.tz
        )
        if not idx[start_pos:end_pos].equals(expected_grid):
            continue

        highs = window["high"].to_numpy(dtype=float)
        lows = window["low"].to_numpy(dtype=float)
        close_end = float(window["close"].iloc[-1])

        high_extreme = float(highs.max())
        low_extreme = float(lows.min())
        up_move_pct = (high_extreme - ref_price) / ref_price * 100.0
        down_move_pct = (ref_price - low_extreme) / ref_price * 100.0
        spike_size = max(up_move_pct, down_move_pct)
        # tie -> naik: arbitrer, statistiknya netral (didokumenkan demi determinisme)
        direction = 1 if up_move_pct >= down_move_pct else -1
        extreme = high_extreme if direction == 1 else low_extreme
        excursion = abs(extreme - ref_price)
        if excursion == 0:
            retracement_pct = 100.0  # tak ada impuls sama sekali = sepenuhnya "balik"
        elif direction == 1:
            retracement_pct = max(0.0, (extreme - close_end) / excursion * 100.0)
        else:
            retracement_pct = max(0.0, (close_end - extreme) / excursion * 100.0)

        move_pct = (close_end - ref_price) / ref_price * 100.0
        # catatan: retrace <= 50% secara aljabar menjamin close masih di sisi
        # arah impuls, jadi `continued` <=> NOT `is_reversed`
        continued = retracement_pct <= CONTINUATION_RETRACEMENT_MAX_PCT
        results.append(HorizonMetrics(
            event_id=event_id,
            ticker=ticker.upper(),
            event_type=event_type,
            release_at=release_at,
            horizon_minutes=minutes,
            reference_price=ref_price,
            spike_size_pct=round(spike_size, 6),
            initial_direction=direction,
            move_at_horizon_pct=round(move_pct, 6),
            retracement_pct=round(retracement_pct, 6),
            continued=bool(continued),
            is_reversed=bool(retracement_pct > CONTINUATION_RETRACEMENT_MAX_PCT),
        ))
    return tuple(results)


def build_pattern_library(
    events: list[EconomicEvent],
    m1_loader: M1Loader,
    horizons: tuple[int, ...] = HORIZON_MINUTES,
) -> tuple[PatternSummary, ...]:
    """Iterasi rilis -> pengukuran -> agregasi per (ticker, event_type, horizon).

    Rilis tanpa data m1 lengkap dilewati dengan log; agregasi hanya dari
    sampel yang valid. Return diurutkan stabil utk reproducibility.
    """
    cache: dict[str, pd.DataFrame | None] = {}
    observations: list[HorizonMetrics] = []
    for event in events:
        # cache eksplisit: setdefault mengevaluasi argumen secara eager sehingga
        # loader terpanggil per-EVENT (IO O(events)) - bug klasik yang sudah difix
        if event.ticker not in cache:
            cache[event.ticker] = m1_loader(event.ticker)
        df = cache[event.ticker]
        if df is None or df.empty:
            logger.debug("m1 kosong utk %s - rilis %s dilewati",
                         event.ticker, event.event_id)
            continue
        observations.extend(measure_release_window(
            df,
            event.scheduled_at,
            event_id=event.event_id,
            ticker=event.ticker,
            event_type=event.event_type,
            horizons=horizons,
        ))

    grouped: dict[tuple[str, str, int], list[HorizonMetrics]] = {}
    for obs in observations:
        grouped.setdefault((obs.ticker, obs.event_type, obs.horizon_minutes), []).append(obs)

    summaries: list[PatternSummary] = []
    for (ticker, etype, minutes), group in sorted(grouped.items()):
        n = len(group)
        directions = [o.initial_direction for o in group]
        spikes = [o.spike_size_pct for o in group]
        retrace = [o.retracement_pct for o in group]
        summaries.append(PatternSummary(
            ticker=ticker,
            event_type=etype,
            horizon_minutes=minutes,
            sample_count=n,
            prob_initial_up=sum(1 for d in directions if d == 1) / n,
            avg_spike_pct=float(np.mean(spikes)),
            p90_spike_pct=float(np.percentile(spikes, 90)),
            median_retracement_pct=float(np.median(retrace)),
            prob_continuation=sum(1 for o in group if o.continued) / n,
            prob_reversal=sum(1 for o in group if o.is_reversed) / n,
        ))
    return tuple(summaries)
