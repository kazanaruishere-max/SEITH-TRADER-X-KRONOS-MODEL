"""GATE-A iterasi: sensitivitas biaya MEDIUM (#2) + mean-reversion (#3).

Pre-register sebelum eksekusi (integritas anti-cherry-picking):
- H2: dgn spread-cap 3x utk event non-HIGH, bucket jobless bergerak dari
  negatif ke PF>1 net -> edge mungkin ada tapi RAWAN BIAYA.
- H3: zona fade (cont<=0.35) arah berlawanan impuls menghasilkan net positif.
Semua di split walk-forward IDENTIK dgn baseline Gate-A.
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


def _loader():
    settings = get_settings()

    def loader(ticker: str):
        df = load_ohlcv(ticker, Timeframe.M1, settings=settings)
        return df.sort_index() if df is not None and not df.empty else None

    return loader, settings


def _summarize(tag: str, report, out: dict) -> None:
    print(f"\n=== {tag} ===")
    print(f"bulan uji={report.n_months_tested} trade={report.n_trades_total} "
          f"skip-gate={report.skipped_no_pattern_or_gate} skip-grid={report.skipped_no_grid}")
    rows = []
    for s in report.stats:
        if s.n_trades < 5:
            continue
        pf = s.profit_factor
        print(f"{s.ticker:9} {s.event_type:18} {s.horizon_minutes:>3}m n={s.n_trades:<4} "
              f"win={s.win_rate*100:>4.0f}% net={s.total_net_pct:>7.2f}% "
              f"dd={s.max_drawdown_pct:>5.2f}% pfG={pf if pf is not None else '-'}")
        rows.append(asdict(s))
    out[tag] = {
        "n_months_tested": report.n_months_tested,
        "n_trades_total": report.n_trades_total,
        "buckets": rows,
    }


def main() -> int:
    loader, settings = _loader()
    events = load_economic_events(settings=settings)
    print(f"events loaded : {len(events)}")

    out: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "design": "walk-forward identik baseline Gate-A",
    }

    r_base = run_walk_forward(events, loader, horizons=HORIZONS, seed_months=4,
                              min_samples=10, min_continuation_prob=0.55)
    _summarize("baseline_continuation", r_base, out)

    r_cap = run_walk_forward(events, loader, horizons=HORIZONS, seed_months=4,
                             min_samples=10, min_continuation_prob=0.55,
                             medium_spread_cap=3.0)
    _summarize("continuation_medium_cap3x (H2)", r_cap, out)

    r_mr = run_walk_forward(events, loader, horizons=HORIZONS, seed_months=4,
                            min_samples=10, direction_mode="mean_reversion")
    _summarize("mean_reversion_fade<=35% (H3)", r_mr, out)

    path = settings.data_dir / "backtests" / "news_gate_a_iteration.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nsaved: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
