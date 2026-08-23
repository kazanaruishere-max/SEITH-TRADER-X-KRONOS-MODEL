"""Backtest layer SEITH: SMA-crossover sweep + walk-forward + tearsheet.

Tujuan P3 (PRD FR-R1..R3): membuktikan mekanisme riset di atas data Parquet
kita sendiri - parameter sweep vektorisasi, evaluasi out-of-sample, model biaya
realistis (fee taker Binance 0.1% + slippage 0.05% default), tearsheet HTML +
stats JSON tersimpan untuk dashboard.

CATATAN JUJUR: strategi SMA-cross adalah KENDARAAN VALIDASI infrastruktur,
bukan alpha final. Signal produksi datang dari pipeline analysis (P2); modul
ini menyediakan mesin pengukurannya.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import vectorbt as vbt
from pydantic import TypeAdapter
from seith_core.config import AppSettings, get_settings
from seith_core.schemas import Ticker, Timeframe
from seith_data.store import load_ohlcv

logger = logging.getLogger("seith.backtest")

DEFAULT_FEES = 0.001
DEFAULT_SLIPPAGE = 0.0005
_validate_ticker = TypeAdapter(Ticker)


def _metrics(pf: vbt.Portfolio) -> dict[str, float]:
    stats = pf.stats()

    def _f(key: str) -> float:
        value = stats.get(key)
        return float(value) if pd.notna(value) else float("nan")

    return {
        "total_return": _f("Total Return [%]"),
        "sharpe": _f("Sharpe Ratio"),
        "max_drawdown": _f("Max Drawdown [%]"),
        "trades": _f("Total Trades"),
        "win_rate": _f("Win Rate [%]"),
    }


def _finite_sharpe(metrics: dict[str, Any]) -> float:
    sharpe = metrics.get("sharpe", float("nan"))
    return sharpe if np.isfinite(sharpe) else float("-inf")


def sweep_sma_cross(
    close: pd.Series,
    *,
    freq: str,
    fast_windows: range | list[int],
    slow_windows: range | list[int],
    fees: float = DEFAULT_FEES,
    slippage: float = DEFAULT_SLIPPAGE,
    init_cash: float = 10_000.0,
) -> tuple[vbt.Portfolio, tuple[int, int]]:
    """Sweep semua pasangan (fast, slow), return portfolio gabungan + best pair by Sharpe."""
    windows = sorted(set(list(fast_windows) + list(slow_windows)))
    fast_ma, slow_ma = vbt.MA.run_combs(
        close, window=np.array(windows), r=2, short_names=["fast", "slow"]
    )
    entries = fast_ma.ma_crossed_above(slow_ma)
    exits = fast_ma.ma_crossed_below(slow_ma)
    portfolio = vbt.Portfolio.from_signals(
        close,
        entries,
        exits,
        init_cash=init_cash,
        fees=fees,
        slippage=slippage,
        freq=freq,
    )
    rets = portfolio.total_return()
    sharpes = portfolio.sharpe_ratio()
    best_idx, best_score = None, float("-inf")
    for i in range(len(rets)):
        s = float(sharpes.iloc[i]) if pd.notna(sharpes.iloc[i]) else float("-inf")
        if np.isfinite(s) and s > best_score:
            best_score, best_idx = s, i
    if best_idx is None:
        raise RuntimeError("tidak ada kombinasi parameter menghasilkan trade valid")
    pair = rets.index[best_idx]
    return portfolio[pair], (int(pair[0]), int(pair[1]))


def run_backtest(
    ticker: str,
    timeframe: Timeframe = Timeframe.H1,
    days: int = 90,
    fees: float = DEFAULT_FEES,
    slippage: float = DEFAULT_SLIPPAGE,
    init_cash: float = 10_000.0,
    split_ratio: float = 0.7,
    settings: AppSettings | None = None,
) -> dict[str, Any]:
    """Full pipeline: load -> sweep in-sample -> eval best di out-of-sample -> artifacts."""
    safe_ticker = _validate_ticker.validate_python(ticker)
    df = load_ohlcv(safe_ticker, timeframe, settings)
    if df is None or len(df) < 60:
        raise RuntimeError(f"data {safe_ticker} {timeframe} kurang dari 60 bar; backfill dulu")
    close = df["close"]
    cutoff = int(len(close) * split_ratio)
    insample, outsample = close.iloc[:cutoff], close.iloc[cutoff:]
    logger.info(
        "[%s] %s: %d bar (%d IS / %d OOS)",
        safe_ticker,
        timeframe.value,
        len(close),
        len(insample),
        len(outsample),
    )

    _, best_pair = sweep_sma_cross(
        insample,
        freq=timeframe.value,
        fast_windows=range(3, 21, 2),
        slow_windows=range(10, 61, 5),
        fees=fees,
        slippage=slippage,
        init_cash=init_cash,
    )
    logger.info("[%s] best in-sample SMA %d/%d", safe_ticker, *best_pair)

    oos_pf, _ = sweep_sma_cross_fixed(
        close,
        timeframe=timeframe,
        fast=best_pair[0],
        slow=best_pair[1],
        start=cutoff,
        fees=fees,
        slippage=slippage,
        init_cash=init_cash,
    )
    result = {
        "ticker": safe_ticker,
        "timeframe": timeframe.value,
        "bars_total": int(len(close)),
        "split_bars": {"insample": cutoff, "outsample": len(close) - cutoff},
        "params": {"fast": best_pair[0], "slow": best_pair[1]},
        "costs": {"fees": fees, "slippage": slippage},
        "outsample": _metrics(oos_pf),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    _save_artifacts(oos_pf, result, settings)
    return result


def sweep_sma_cross_fixed(
    close: pd.Series,
    *,
    timeframe: Timeframe,
    fast: int,
    slow: int,
    start: int,
    fees: float,
    slippage: float,
    init_cash: float,
) -> tuple[vbt.Portfolio, tuple[int, int]]:
    """Portfolio OOS memakai pair terpilih; entry hanya dievaluasi mulai `start`."""
    segment = close.iloc[start:]
    fast_ma = vbt.MA.run(segment, window=fast)
    slow_ma = vbt.MA.run(segment, window=slow)
    pf = vbt.Portfolio.from_signals(
        segment,
        fast_ma.ma_crossed_above(slow_ma),
        fast_ma.ma_crossed_below(slow_ma),
        init_cash=init_cash,
        fees=fees,
        slippage=slippage,
        freq=timeframe.value,
    )
    return pf, (fast, slow)


def _save_artifacts(
    pf: vbt.Portfolio, result: dict[str, Any], settings: AppSettings | None
) -> None:
    s = settings or get_settings()
    base = s.data_dir / "backtests" / result["ticker"] / result["timeframe"]
    base.mkdir(parents=True, exist_ok=True)
    html_path = base / "tearsheet.html"
    try:
        fig = pf.plot()
        fig.write_html(html_path)
        result["tearsheet"] = f"backtests/{result['ticker']}/{result['timeframe']}/tearsheet.html"
    except Exception as exc:  # noqa: BLE001 - plot gagal tidak boleh membatalkan stats
        logger.warning("tearsheet gagal dibuat: %s", exc)
    (base / "stats.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    logger.info("artifacts -> %s", base)


def main() -> None:
    parser = argparse.ArgumentParser(description="SEITH backtest runner")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")

    result = run_backtest(args.ticker, Timeframe(args.timeframe), args.days)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
