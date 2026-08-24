"""News Trigger Engine (E3): rilis + surprise + pola library -> Signal NEWS_EVENT.

ATURAN KESELAMATAN (skill seith-trading-safety, Tier-0):
- Modul ini HANYA menghasilkan `Signal` (arah + confidence). Sizing, approval,
  dan eksekusi sepenuhnya jalur EXISTING: proposal PENDING_APPROVAL -> approve
  manusia -> RiskManager -> intake. Tidak ada pintu order di sini.
- Sinyal hanya lahir untuk rilis yang SUDAH keluar angkanya (actual terisi)
  dan masih dalam window aktif pasca-rilis.
- Pattern library harus punya sampel statistik yang layak sebelum boleh
  memicu apa pun - tidak ada trading berdasar pola tanpa bukti.

Ditaruhkan di seith-core karena dipakai lintas service: analysis memproduksi
PatternSummary, trader news bridge mengevaluasi trigger - keduanya kontrak ini.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from seith_core.schemas import (
    Action,
    EconomicEvent,
    EventImportance,
    PatternSummary,
    Signal,
    SignalSource,
)

logger = logging.getLogger("seith.news_trigger")

#: Rank importance SATU sumber kebenaran - dipakai trigger & events store.
IMPORTANCE_RANK = {
    EventImportance.LOW: 1,
    EventImportance.MEDIUM: 2,
    EventImportance.HIGH: 3,
}


def deterministic_signal_id(event: EconomicEvent) -> str:
    """signal_id deterministik dari natural key event (anti double-create).

    BUKAN event.event_id (tidak stabil antar-refetch - lihat schema docstring).
    Bridge yang scan berulang atas rilis sama HARUS menghasilkan id identik;
    uniknya ditegakkan UNIQUE INDEX di tabel order_proposals.
    """
    natural = f"{event.ticker}|{event.event_type}|{event.scheduled_at.astimezone(UTC).isoformat()}"
    return "sig_" + hashlib.sha256(natural.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class TriggerConfig:
    """Threshold trigger; nilai konservatif default - revisi setelah backtest."""

    min_importance: EventImportance = EventImportance.HIGH
    #: sinyal hanya valid N menit setelah scheduled_at (impuls news cepat mati)
    signal_window_minutes: int = 30
    min_pattern_samples: int = 10
    min_continuation_prob: float = 0.55
    horizon_minutes: int = 15
    #: bila True, arah Kronos wajib searah dgn pola; None = ditolak
    require_kronos_agree: bool = False


def _pattern_for(
    library: Sequence[PatternSummary],
    ticker: str,
    event_type: str,
    horizon_minutes: int,
) -> PatternSummary | None:
    for p in library:
        if (
            p.ticker == ticker.upper()
            and p.event_type == event_type
            and p.horizon_minutes == horizon_minutes
        ):
            return p
    return None


def _action_from_prob(prob_up: float) -> Action:
    if prob_up > 0.5:
        return Action.BUY
    if prob_up < 0.5:
        return Action.SELL
    return Action.HOLD


def evaluate_release(
    event: EconomicEvent,
    pattern_library: Sequence[PatternSummary],
    now: datetime,
    kronos_direction: int | None = None,
    config: TriggerConfig | None = None,
) -> Signal | None:
    """Evaluasi satu rilis yang sudah keluar -> Signal atau None dengan alasan log.

    `now` wajib timezone-aware (fail-fast konsisten konvensi proyek); caller
    production memakai waktu UTC nyata.
    """
    cfg = config or TriggerConfig()
    if now.tzinfo is None:
        raise ValueError("'now' wajib timezone-aware")
    now = now.astimezone(UTC)

    if event.actual is None:
        logger.debug("rilis %s belum keluar actual - skip", event.event_id)
        return None
    if event.surprise_factor is None:
        logger.debug("surprise tidak terdefinisi utk %s - skip", event.event_id)
        return None
    if IMPORTANCE_RANK[event.importance] < IMPORTANCE_RANK[cfg.min_importance]:
        logger.debug("importance %s < threshold %s - skip",
                     event.importance, cfg.min_importance)
        return None
    age_min = (now - event.scheduled_at.astimezone(UTC)).total_seconds() / 60.0
    if not 0 <= age_min <= cfg.signal_window_minutes:
        logger.debug("usia rilis %.1f menit di luar window %d - skip",
                     age_min, cfg.signal_window_minutes)
        return None

    pattern = _pattern_for(
        pattern_library, event.ticker, event.event_type, cfg.horizon_minutes
    )
    if pattern is None:
        logger.debug("tidak ada pola utk (%s,%s)@%dm - skip",
                     event.ticker, event.event_type, cfg.horizon_minutes)
        return None
    if pattern.sample_count < cfg.min_pattern_samples:
        logger.debug("pola hanya %d sampel (<%d) - skip",
                     pattern.sample_count, cfg.min_pattern_samples)
        return None
    if pattern.prob_continuation < cfg.min_continuation_prob:
        logger.debug("prob lanjut %.2f < %.2f - skip",
                     pattern.prob_continuation, cfg.min_continuation_prob)
        return None

    # Arah strategi continuation: ikuti impuls awal historis pola.
    # Pola arah netral (prob tepat 0.5) = tidak ada edge -> bukan kandidat trade.
    action = _action_from_prob(pattern.prob_initial_up)
    if action is Action.HOLD:
        logger.debug("pola netral (up=%.2f) - tidak ada edge - skip",
                     pattern.prob_initial_up)
        return None
    direction = 1 if action is Action.BUY else -1

    # Konfirmasi Kronos (opsional): forecast foundation model wajib searah bila
    # diwajibkan config - divergensi model = konflik sinyal, jangan trade.
    if cfg.require_kronos_agree:
        if kronos_direction is None or kronos_direction != direction:
            logger.info("%s: Kronos tidak searah (%s vs %d) - sinyal dibatalkan",
                        event.event_id, kronos_direction, direction)
            return None

    # Kontribusi surprise dibatasi [-1,+1] agar confidence tetap proporsional.
    surprise_term = min(abs(event.surprise_factor), 1.0)
    confidence = min(0.6 * pattern.prob_continuation + 0.4 * surprise_term, 0.99)
    confidence = round(confidence, 4)
    rationale = (
        f"{event.event_type} ({event.currency}) surprise "
        f"{event.surprise_factor:+.2%}; pola {pattern.ticker}@"
        f"{pattern.horizon_minutes}m n={pattern.sample_count} "
        f"cont={pattern.prob_continuation:.0%} up={pattern.prob_initial_up:.0%}; "
        f"kronos={'agree' if kronos_direction == direction else 'n/a'}"
    )
    signal = Signal(
        signal_id=deterministic_signal_id(event),
        ticker=event.ticker,
        asset_class=event.asset_class,
        action=action,
        confidence=confidence,
        source=SignalSource.NEWS_EVENT,
        rationale=rationale,
        strategy_hint=f"news:{event.event_type}@{cfg.horizon_minutes}m:{event.event_id}",
    )
    logger.info("SINYAL NEWS_EVENT %s %s conf=%.2f (%s)",
                signal.ticker, signal.action.value, confidence, rationale[:80])
    return signal


def scan_releases(
    events: Sequence[EconomicEvent],
    pattern_library: Sequence[PatternSummary],
    now: datetime,
    kronos_directions: dict[str, int] | None = None,
    config: TriggerConfig | None = None,
) -> list[Signal]:
    """Scan kumpulan rilis (mis. hasil load_economic_events window terakhir)."""
    kronos_directions = kronos_directions or {}
    signals: list[Signal] = []
    for event in events:
        signal = evaluate_release(
            event,
            pattern_library,
            now=now,
            kronos_direction=kronos_directions.get(event.event_id),
            config=config,
        )
        if signal is not None:
            signals.append(signal)
    return signals
