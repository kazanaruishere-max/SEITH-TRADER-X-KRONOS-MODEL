"""P1 GATE-A#2: Kronos standalone walk-forward evaluation (anti-lookahead).

Pertanyaan yang dijawab: apakah expected_return Kronos punya RankIC nyata
terhadap realized forward return — dan lebih baik dari baseline persistence
(momentum naif h-bar terakhir)?

Invariant anti-leak (Tier-0 harness):
- Window input model berakhir TEPAT di cutoff t (bar t adalah bar terakhir
  yang "diketahui").
- Label future close[t+h] HANYA dipakai sebagai y, tidak pernah masuk input.
- Baseline persistence memakai data yang sama dengannya (close[t-h]).

Semua fungsi metrik pure & deterministic; inference GPU disuntik lewat
``forecast_fn`` agar unit test berjalan tanpa GPU/jaringan.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
from seith_core.config import AppSettings, get_settings
from seith_core.schemas import Timeframe
from seith_data.store import load_ohlcv

logger = logging.getLogger(__name__)

#: fn(window_df_ending_at_cutoff) -> predicted expected_return (float)
ForecastFn = Callable[[pd.DataFrame], float]


def rank_ic(pred: list[float], real: list[float]) -> float:
    """Spearman rank correlation pred vs real; NaN bila degenerate."""
    if len(pred) != len(real) or len(pred) < 3:
        return float("nan")
    s_pred, s_real = pd.Series(pred, dtype=float), pd.Series(real, dtype=float)
    if s_pred.nunique() < 2 or s_real.nunique() < 2:
        return float("nan")
    ic = s_pred.corr(s_real, method="spearman")
    return float("nan") if pd.isna(ic) else float(ic)


def hit_rate(pred: list[float], real: list[float]) -> float:
    """Fraksi arah prediksi == arah realized (abaikan pasangan dengan real==0)."""
    pairs = [(p, r) for p, r in zip(pred, real, strict=True) if r != 0.0]
    if not pairs:
        return float("nan")
    correct = sum(1 for p, r in pairs if np.sign(p) == np.sign(r))
    return correct / len(pairs)


@dataclass(frozen=True)
class EvalReport:
    ticker: str
    timeframe: str
    horizon_bars: int
    lookback: int
    n_windows: int
    ic_kronos: float
    ic_persistence: float
    hit_kronos: float
    hit_persistence: float
    mean_abs_pred: float
    detail: list[dict] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


def _default_forecast_fn(
    ticker: str, timeframe: Timeframe, horizon_bars: int, lookback: int, sample_count: int
) -> ForecastFn:
    """Inference Kronos asli pada window historis arbitrer (anti-leak by slicing)."""
    from seith_analysis.kronos_service import forecast

    def _fn(window: pd.DataFrame) -> float:
        fc = forecast(
            ticker,
            timeframe,
            horizon_bars,
            lookback=lookback,
            sample_count=sample_count,
            df=window,
        )
        return float(fc.expected_return)

    return _fn


def evaluate_pair(
    ticker: str,
    timeframe: Timeframe = Timeframe.H1,
    horizon_bars: int = 24,
    lookback: int = 400,
    stride: int = 40,
    max_windows: int | None = None,
    forecast_fn: ForecastFn | None = None,
    settings: AppSettings | None = None,
    df: pd.DataFrame | None = None,
) -> EvalReport:
    """Walk-forward RankIC satu pair.

    Cutoff t artinya bar terakhir yang diketahui = index t (inclusive).
    ``df`` opsional untuk injection di unit test; default baca store.
    """
    if df is None:
        df = load_ohlcv(ticker, timeframe, settings)
    if df is None or len(df) < lookback + horizon_bars + 1:
        raise RuntimeError(
            f"data {ticker} {timeframe} kurang "
            f"({0 if df is None else len(df)} bar; butuh >= {lookback + horizon_bars + 1})"
        )
    fn = forecast_fn or _default_forecast_fn(
        ticker, timeframe, horizon_bars, lookback, sample_count=8
    )

    closes = df["close"].astype(float).to_numpy()
    ts = df.index.to_series()
    last = len(df) - 1 - horizon_bars
    cutoffs = list(range(lookback - 1, last + 1, stride))
    if max_windows is not None:
        cutoffs = cutoffs[:max_windows]
    if len(cutoffs) < 3:
        raise RuntimeError(
            f"cutoff walk-forward kurang ({len(cutoffs)}); perbanyak data atau"
            f" kecilkan stride/lookback"
        )

    preds_k: list[float] = []
    preds_p: list[float] = []
    reals: list[float] = []
    detail: list[dict] = []
    for t in cutoffs:
        window = df.iloc[t - lookback + 1 : t + 1]
        assert len(window) == lookback, "window harus tepat lookback bar"
        pred_k = float(fn(window))
        pred_p = float(closes[t] / closes[t - horizon_bars] - 1.0)
        real = float(closes[t + horizon_bars] / closes[t] - 1.0)
        preds_k.append(pred_k)
        preds_p.append(pred_p)
        reals.append(real)
        detail.append(
            {
                "cutoff": str(ts.iloc[t]),
                "pred_kronos": pred_k,
                "pred_persistence": pred_p,
                "realized": real,
            }
        )

    report = EvalReport(
        ticker=ticker,
        timeframe=timeframe.value,
        horizon_bars=horizon_bars,
        lookback=lookback,
        n_windows=len(cutoffs),
        ic_kronos=rank_ic(preds_k, reals),
        ic_persistence=rank_ic(preds_p, reals),
        hit_kronos=hit_rate(preds_k, reals),
        hit_persistence=hit_rate(preds_p, reals),
        mean_abs_pred=float(np.mean(np.abs(preds_k))) if preds_k else float("nan"),
        detail=detail,
    )
    logger.info(
        "[%s] RankIC kronos=%.3f persist=%.3f hit=%.2f/%.2f (n=%d)",
        ticker,
        report.ic_kronos,
        report.ic_persistence,
        report.hit_kronos,
        report.hit_persistence,
        report.n_windows,
    )
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Kronos walk-forward RankIC eval")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--horizon", type=int, default=24)
    parser.add_argument("--stride", type=int, default=40)
    parser.add_argument("--max-windows", type=int, default=None)
    args = parser.parse_args()
    get_settings()  # fail-fast env
    report = evaluate_pair(
        args.ticker,
        timeframe=Timeframe(args.timeframe),
        horizon_bars=args.horizon,
        stride=args.stride,
        max_windows=args.max_windows,
    )
    print(report.to_json())


if __name__ == "__main__":
    main()
