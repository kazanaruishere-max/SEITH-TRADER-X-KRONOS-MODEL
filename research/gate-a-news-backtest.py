"""GATE-A runner: backtest walk-forward strategi news pada data riil.

Jalankan dari workdir apps/analysis dengan env SEITH_* terisi:
    uv run python ../../research/gate-a-news-backtest.py

Output: data/backtests/news_gate_a.json + laporan teks + verdict threshold.
Threshold go/no-go (owner-approved, bisa dinego): hit-rate >= 52%,
max drawdown <= 10%, profit factor > 1.0, n_trades cukup (>= 20 per bucket
dianggap layak dipercaya; di bawah itu hanya indikatif).
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime

from seith_core.config import get_settings
from seith_core.schemas import Timeframe

from seith_analysis.backtest_news import run_walk_forward
from seith_data.events_store import load_economic_events
from seith_data.store import load_ohlcv

HORIZONS = (5, 15, 60)
SEED_MONTHS = 4
MIN_SAMPLES = 10
MIN_CONTINUATION = 0.55
THRESHOLD_WINRATE = 0.52
THRESHOLD_MAX_DD = 10.0


def main() -> int:
    settings = get_settings()
    events = load_economic_events(settings=settings)
    print(f"events loaded : {len(events)}")

    def loader(ticker: str):
        df = load_ohlcv(ticker, Timeframe.M1, settings=settings)
        return df.sort_index() if df is not None and not df.empty else None

    report = run_walk_forward(
        events,
        loader,
        horizons=HORIZONS,
        seed_months=SEED_MONTHS,
        min_samples=MIN_SAMPLES,
        min_continuation_prob=MIN_CONTINUATION,
    )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "design": "walk-forward expanding-window, library strictly-past",
        "horizons": list(HORIZONS),
        "seed_months": SEED_MONTHS,
        "min_samples": MIN_SAMPLES,
        "min_continuation": MIN_CONTINUATION,
        "n_months_tested": report.n_months_tested,
        "train_cutoffs": [c.isoformat() for c in report.train_cutoffs],
        "n_trades_total": report.n_trades_total,
        "skipped_no_grid": report.skipped_no_grid,
        "skipped_no_pattern_or_gate": report.skipped_no_pattern_or_gate,
        "thresholds": {
            "winrate": THRESHOLD_WINRATE,
            "max_dd_pct": THRESHOLD_MAX_DD,
        },
        "buckets": [asdict(s) for s in report.stats],
    }
    out = settings.data_dir / "backtests" / "news_gate_a.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"saved         : {out}")
    print(f"bulan uji     : {report.n_months_tested} | trade: {report.n_trades_total}"
          f" | skip-grid: {report.skipped_no_grid}"
          f" | skip-gate: {report.skipped_no_pattern_or_gate}")

    print("\n=== HASIL PER BUCKET (net % kumulatif, biaya spread termasuk) ===")
    print(f"{'ticker':9} {'event_type':18} {'h':>4} {'n':>4} {'win%':>6} "
          f"{'net%':>8} {'dd%':>6} {'pf':>6} {'verdict':>9}")
    for s in report.stats:
        if s.n_trades < 3:
            continue
        pf = s.profit_factor
        pf_s = f"{pf:.1f}" if pf is not None else "-"
        credible = s.n_trades >= 20
        passes = (
            s.win_rate >= THRESHOLD_WINRATE
            and s.max_drawdown_pct <= THRESHOLD_MAX_DD
            and (pf is None or pf > 1.0)
            and s.total_net_pct > 0
        )
        verdict = ("PASS" if passes else "FAIL") + ("" if credible else "*")
        print(f"{s.ticker:9} {s.event_type:18} {s.horizon_minutes:>3}m "
              f"{s.n_trades:>4} {s.win_rate*100:>5.0f}% {s.total_net_pct:>7.2f}% "
              f"{s.max_drawdown_pct:>5.2f}% {pf_s:>6} {verdict:>9}")
    print("\n(* = n<20 -> indikatif, belum layak jadi dasar keputusan)")

    strong = [
        s for s in report.stats
        if s.total_net_pct > 0 and s.win_rate >= THRESHOLD_WINRATE
        and s.max_drawdown_pct <= THRESHOLD_MAX_DD and s.profit_factor not in (None,)
        and s.n_trades >= 20
    ]
    print(f"\nVERDICT GATE-A: {len(strong)} bucket lolos threshold penuh"
          f" (win>={THRESHOLD_WINRATE:.0%}, dd<={THRESHOLD_MAX_DD}%, pf>1, n>=20).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
