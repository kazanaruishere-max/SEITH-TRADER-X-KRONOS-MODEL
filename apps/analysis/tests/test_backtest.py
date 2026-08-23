"""Test backtest layer: sweep, walk-forward, biaya, artifacts - semua sintetis lokal."""

import json

import numpy as np
import pandas as pd
import pytest
from seith_core.config import AppSettings
from seith_core.schemas import Timeframe
from seith_data.store import save_ohlcv

from seith_analysis.backtest import _metrics, run_backtest, sweep_sma_cross


def make_ohlcv(tmp_path, n=300, trend=0.08, noise=0.4, seed=7):
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(trend, noise, n))
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": 1000.0,
        },
        index=idx,
    )
    settings = AppSettings(_env_file=None, data_dir=tmp_path / "d", db_path=tmp_path / "t.db")
    save_ohlcv(df.astype("float64"), "BTCUSDT", Timeframe.H1, settings)
    return settings, df["close"]


class TestSweep:
    def test_returns_best_pair_and_finite(self, tmp_path):
        _, close = make_ohlcv(tmp_path)
        pf, pair = sweep_sma_cross(
            close, freq="1h", fast_windows=range(3, 10, 2), slow_windows=range(10, 31, 5)
        )
        fast, slow = pair
        assert fast < slow
        assert np.isfinite(pf.total_return())

    def test_sweep_deterministic(self, tmp_path):
        _, close = make_ohlcv(tmp_path)
        _, pair_a = sweep_sma_cross(
            close, freq="1h", fast_windows=range(3, 10, 2), slow_windows=range(10, 31, 5)
        )
        _, pair_b = sweep_sma_cross(
            close, freq="1h", fast_windows=range(3, 10, 2), slow_windows=range(10, 31, 5)
        )
        assert pair_a == pair_b


class TestMetrics:
    def test_metrics_keys_and_types(self):
        idx = pd.date_range("2026-01-01", periods=50, freq="1h", tz="UTC")
        price = pd.Series(np.linspace(100, 110, 50), index=idx)
        raw = price.pct_change().fillna(0.0) > 0
        entries = raw.shift(1).fillna(False).astype(bool)
        exits = (~raw).shift(1).fillna(True).astype(bool)
        from vectorbt import Portfolio

        pf = Portfolio.from_signals(price, entries, exits, init_cash=1000, freq="1h")
        m = _metrics(pf)
        assert set(m) == {"total_return", "sharpe", "max_drawdown", "trades", "win_rate"}
        assert isinstance(m["trades"], float)


class TestRunBacktest:
    def test_full_pipeline_artifacts(self, tmp_path):
        settings, _ = make_ohlcv(tmp_path, n=400)
        result = run_backtest("btcusdt", Timeframe.H1, days=90, settings=settings)
        assert result["ticker"] == "BTCUSDT"
        assert result["params"]["fast"] < result["params"]["slow"]
        assert result["split_bars"]["insample"] > result["split_bars"]["outsample"] * 1.5
        base = settings.data_dir / "backtests" / "BTCUSDT" / "1h"
        stats = json.loads((base / "stats.json").read_text(encoding="utf-8"))
        assert stats["outsample"]["total_return"] is not None
        assert (base / "tearsheet.html").exists()

    def test_fees_reduce_return_fixed_params(self, tmp_path):
        settings, close = make_ohlcv(tmp_path)
        from seith_analysis.backtest import sweep_sma_cross_fixed

        free_pf, _ = sweep_sma_cross_fixed(
            close,
            timeframe=Timeframe.H1,
            fast=5,
            slow=20,
            start=100,
            fees=0.0,
            slippage=0.0,
            init_cash=10_000,
        )
        paid_pf, _ = sweep_sma_cross_fixed(
            close,
            timeframe=Timeframe.H1,
            fast=5,
            slow=20,
            start=100,
            fees=0.001,
            slippage=0.0005,
            init_cash=10_000,
        )
        assert paid_pf.total_return() <= free_pf.total_return()

    def test_insufficient_data_raises(self, tmp_path):
        settings, _ = make_ohlcv(tmp_path, n=40)
        with pytest.raises(RuntimeError, match="kurang"):
            run_backtest("BTCUSDT", Timeframe.H1, days=90, settings=settings)
