"""Unit test kronos_eval: metrik pure + invariant anti-leak walk-forward.

Semua test tanpa GPU/jaringan — inference disuntik lewat forecast_fn stub.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from seith_analysis.kronos_eval import evaluate_pair, hit_rate, rank_ic


def _synthetic_df(n: int = 600, slope: float = 0.1) -> pd.DataFrame:
    """OHLCV deterministik: random-walk volume + close linier (untuk persistence)."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    close = 100.0 + slope * np.arange(n)
    high = close * (1 + 0.001)
    low = close * (1 - 0.001)
    open_ = close - slope  # bar terbuka dekat penutupan sebelumnya
    vol = rng.uniform(10, 100, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


# ---------- metrik pure ----------


def test_rank_ic_perfect_monotonic() -> None:
    assert rank_ic([1.0, 2.0, 3.0], [10.0, 20.0, 30.0]) == pytest.approx(1.0)


def test_rank_ic_reversed() -> None:
    assert rank_ic([1.0, 2.0, 3.0], [30.0, 20.0, 10.0]) == pytest.approx(-1.0)


def test_rank_ic_degenerate_constant_is_nan() -> None:
    assert math.isnan(rank_ic([5.0, 5.0, 5.0], [1.0, 2.0, 3.0]))
    assert math.isnan(rank_ic([1.0], [1.0]))


def test_hit_rate_direction() -> None:
    assert hit_rate([1.0, -1.0], [2.0, -3.0]) == 1.0
    assert hit_rate([1.0, 1.0], [-2.0, 3.0]) == pytest.approx(0.5)
    assert math.isnan(hit_rate([1.0], [0.0]))  # real==0 diabaikan semua


# ---------- walk-forward anti-leak ----------


def test_evaluate_pair_windows_end_exactly_at_cutoff() -> None:
    df = _synthetic_df()
    seen_last_ts: list[pd.Timestamp] = []
    seen_lengths: list[int] = []

    def stub(window: pd.DataFrame) -> float:
        seen_last_ts.append(window.index[-1])
        seen_lengths.append(len(window))
        return float(-window["close"].iloc[-1])  # deterministik, bervariasi

    report = evaluate_pair(
        "BTCUSDT",
        horizon_bars=24,
        lookback=400,
        stride=40,
        forecast_fn=stub,
        df=df,
    )

    assert seen_lengths and all(n == 400 for n in seen_lengths)
    # setiap window berakhir TEPAT di cutoff-nya (tidak ada bar future bocor)
    assert [str(x) for x in seen_last_ts] == [d["cutoff"] for d in report.detail]
    assert report.n_windows == len(seen_last_ts) >= 3
    assert report.ic_persistence == pytest.approx(1.0, abs=1e-6)  # close linier


def test_evaluate_pair_insufficient_data_raises() -> None:
    with pytest.raises(RuntimeError, match="kurang"):
        evaluate_pair(
            "BTCUSDT",
            horizon_bars=24,
            lookback=400,
            forecast_fn=lambda w: 0.0,
            df=_synthetic_df(n=100),
        )


def test_evaluate_pair_to_json_roundtrip_fields() -> None:
    import json

    df = _synthetic_df()

    def stub(window: pd.DataFrame) -> float:
        return 0.01 if window["close"].iloc[-1] % 2 < 1 else -0.02

    report = evaluate_pair(
        "BTCUSDT", horizon_bars=24, lookback=400, stride=40, forecast_fn=stub, df=df
    )
    payload = json.loads(report.to_json())
    assert payload["ticker"] == "BTCUSDT"
    assert payload["n_windows"] == report.n_windows
