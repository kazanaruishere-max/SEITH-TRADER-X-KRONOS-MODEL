"""E2 riil: bangun pattern library dari events store + m1 parquet.

Jalankan dari workdir apps/analysis dengan env SEITH_* terisi:
    uv run python ../../research/2026-08-24-build-patterns.py

Output: data/patterns/pattern_library.json + laporan teks terukur.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime

from seith_core.config import get_settings
from seith_core.schemas import Timeframe

from seith_analysis.pattern_library import build_pattern_library
from seith_data.events_store import load_economic_events
from seith_data.store import load_ohlcv

STRONG_N = 10
STRONG_CONT = 0.55


def main() -> int:
    settings = get_settings()
    events = load_economic_events(settings=settings)
    print(f"events loaded : {len(events)}")
    tickers_seen = sorted({e.ticker for e in events})
    print(f"tickers       : {tickers_seen}")

    def loader(ticker: str):
        df = load_ohlcv(ticker, Timeframe.M1, settings=settings)
        if df is None or df.empty:
            return None
        return df.sort_index()  # kontrak: monotonik naik wajib

    summaries = build_pattern_library(events, loader)
    print(f"pattern groups: {len(summaries)}")

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "input_event_count": len(events),
        "summary_count": len(summaries),
        "min_samples_strong": STRONG_N,
        "strong_continuation_threshold": STRONG_CONT,
        "patterns": [asdict(s) for s in summaries],
    }
    out_path = settings.data_dir / "patterns" / "pattern_library.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"saved         : {out_path}")

    print("\n=== LAPORAN POLA PASCA-RILIS (n>=3 ditampilkan) ===")
    print(f"{'ticker':9} {'event_type':22} {'h':>4} {'n':>4} {'up%':>6} "
          f"{'avgSpike':>9} {'p90':>6} {'medRetr':>8} {'cont%':>6} {'rev%':>6}")
    shown = 0
    for s in sorted(summaries, key=lambda x: (x.ticker, x.event_type, x.horizon_minutes)):
        if s.sample_count < 3:
            continue
        marker = ""
        if s.sample_count >= STRONG_N and s.prob_continuation >= STRONG_CONT:
            marker = "  <== KUAT"
        print(f"{s.ticker:9} {s.event_type:22} {s.horizon_minutes:>3}m {s.sample_count:>4} "
              f"{s.prob_initial_up*100:>5.0f}% {s.avg_spike_pct:>8.3f}% "
              f"{s.p90_spike_pct:>5.3f}% {s.median_retracement_pct:>7.1f}% "
              f"{s.prob_continuation*100:>5.0f}% {s.prob_reversal*100:>5.0f}%{marker}")
        shown += 1
    print(f"\nditampilkan {shown} grup (n>=3); tanda KUAT = n>={STRONG_N} "
          f"dengan prob lanjut >={STRONG_CONT:.0%}")

    strong = [s for s in summaries
              if s.sample_count >= STRONG_N and s.prob_continuation >= STRONG_CONT]
    print(f"\nkesimpulan cepat: {len(strong)} bucket KUAT kandidat strategi continuation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
