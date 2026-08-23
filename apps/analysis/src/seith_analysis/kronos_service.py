"""Kronos price-forecast service (GPU lokal).

Wrapper di atas vendored Kronos (vendor/Kronos). Output mengikuti kontrak
`ForecastResult` seith-core. Confidence adalah HEURISTIK fase awal: rasio
volatilitas path prediksi vs volatilitas historis lookback - akan diganti
dengan metrik kalibrasi setelah baseline 30 hari paper.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import threading
from functools import lru_cache
from pathlib import Path

import pandas as pd
from pydantic import TypeAdapter
from seith_core.config import get_settings
from seith_core.schemas import AssetClass, ForecastResult, Ticker, Timeframe
from seith_data.store import load_ohlcv, write_parquet_atomic
from seith_data.timeutil import timeframe_seconds

logger = logging.getLogger("seith.kronos")

_MODEL_INIT_LOCK = threading.Lock()
_validate_ticker = TypeAdapter(Ticker)


def _find_vendor_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "vendor" / "Kronos"
        if (candidate / "model").is_dir():
            return candidate
    raise RuntimeError("vendor/Kronos tidak ditemukan dari lokasi file ini")


_ASSET_CLASS: dict[str, AssetClass] = {
    "binance": AssetClass.CRYPTO,
    "oanda": AssetClass.FOREX,
    "yfinance": AssetClass.EQUITY_US,
}


@lru_cache(maxsize=1)
def _get_predictor():
    vendor_root = _find_vendor_root()
    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))
    cfg = get_settings().kronos
    from model import Kronos, KronosPredictor, KronosTokenizer

    device = cfg.device
    if device == "auto":
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    with _MODEL_INIT_LOCK:
        logger.info("load %s pada device=%s", cfg.model_name, device)
        tokenizer = KronosTokenizer.from_pretrained(cfg.tokenizer_name)
        model = Kronos.from_pretrained(cfg.model_name)
        return KronosPredictor(model, tokenizer, device=device, max_context=512)


def detect_asset_class(ticker: str) -> AssetClass:
    from seith_data.sources import detect_source

    return _ASSET_CLASS[detect_source(ticker)]


def forecast(
    ticker: str,
    timeframe: Timeframe,
    horizon_bars: int,
    lookback: int = 400,
    sample_count: int = 8,
) -> ForecastResult:
    safe_ticker = _validate_ticker.validate_python(ticker)
    if horizon_bars <= 0:
        raise ValueError("horizon_bars wajib positif (fail-fast sebelum inference GPU)")
    df = load_ohlcv(safe_ticker, timeframe)
    if df is None or len(df) < lookback:
        raise RuntimeError(
            f"data {safe_ticker} {timeframe} kurang ({0 if df is None else len(df)} bar);"
            f" backfill minimal {lookback} bar dulu"
        )
    x_df = df.iloc[-lookback:][["open", "high", "low", "close", "volume"]]
    x_ts = x_df.index.to_series()
    # step kanonik dari tabel Timeframe - BUKAN diff 2 bar terakhir
    # (ekor data ber-gap, mis. Jumat->Senin saham/forex, akan merusak horizon)
    step = pd.Timedelta(seconds=timeframe_seconds(timeframe))
    y_ts = pd.Series(pd.date_range(x_ts.iloc[-1] + step, periods=horizon_bars, freq=step))

    predictor = _get_predictor()
    pred = predictor.predict(
        df=x_df,
        x_timestamp=x_ts,
        y_timestamp=y_ts,
        pred_len=horizon_bars,
        T=1.0,
        top_p=0.9,
        sample_count=sample_count,
        verbose=False,
    )

    last_close = float(x_df["close"].iloc[-1])
    if last_close <= 0:
        raise RuntimeError(f"last_close '{safe_ticker}' tidak valid ({last_close})")
    final_close = float(pred["close"].iloc[-1])
    expected_return = final_close / last_close - 1.0

    hist_ret = x_df["close"].pct_change().dropna()
    pred_ret = pred["close"].pct_change().dropna()
    vol_ratio = float(pred_ret.std() / max(hist_ret.std(), 1e-12))
    confidence = min(0.95, max(0.05, math.exp(-2.0 * (vol_ratio - 1.0))))

    # VALIDATE-BEFORE-WRITE: konstruksi ForecastResult (validasi path/ticker)
    # harus lulus SEBELUM file apa pun ditulis ke disk.
    settings = get_settings()
    rel_path = f"parquet/{safe_ticker}/forecast_{timeframe.value}.parquet"
    result = ForecastResult(
        ticker=safe_ticker,
        asset_class=detect_asset_class(safe_ticker),
        timeframe=timeframe,
        horizon_bars=horizon_bars,
        expected_return=round(expected_return, 6),
        confidence=round(confidence, 4),
        ohlcv_path=rel_path,
    )
    out = settings.data_dir / rel_path
    out.parent.mkdir(parents=True, exist_ok=True)
    write_parquet_atomic(pred, out)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="SEITH Kronos forecast")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--horizon", type=int, default=24)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")

    result = forecast(args.ticker, Timeframe(args.timeframe), args.horizon)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
