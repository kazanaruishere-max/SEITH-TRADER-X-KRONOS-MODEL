"""Test seith-data: store round-trip, quality checks, routing, symbol normalization.

Store functions menerima `settings` eksplisit sehingga test isolasi penuh di tmp_path
tanpa menyentuh data/ repo sungguhan.
"""


from datetime import UTC, datetime

import pandas as pd
import pytest
from pydantic import ValidationError
from seith_core.config import AppSettings
from seith_core.schemas import Timeframe

from seith_data.quality import run_checks
from seith_data.sources import detect_source
from seith_data.sources.binance import to_ccxt_symbol
from seith_data.store import (
    finish_run,
    init_db,
    load_ohlcv,
    ohlcv_path,
    save_ohlcv,
    start_run,
    upsert_ohlcv,
)


@pytest.fixture()
def settings(tmp_path):
    return AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "test.db",
    )


def make_df(start: str, hours: int, base_price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=hours, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": base_price,
            "high": base_price * 1.01,
            "low": base_price * 0.99,
            "close": base_price,
            "volume": 10.0,
        },
        index=pd.DatetimeIndex(idx, name="timestamp"),
    ).astype("float64")


class TestStore:
    def test_save_load_round_trip(self, settings):
        df = make_df("2026-08-01", 24)
        path = save_ohlcv(df, "BTCUSDT", Timeframe.H1, settings)
        assert path.exists()
        loaded = load_ohlcv("BTCUSDT", Timeframe.H1, settings)
        assert loaded is not None
        assert len(loaded) == 24
        assert list(loaded.columns) == ["open", "high", "low", "close", "volume"]
        assert str(loaded.index.tz) == "UTC"
        pd.testing.assert_frame_equal(loaded, df, check_freq=False)

    def test_load_missing_returns_none(self, settings):
        assert load_ohlcv("NOPE", Timeframe.H1, settings) is None

    def test_upsert_dedup_keep_last(self, settings):
        old = make_df("2026-08-01", 24)
        upsert_ohlcv(old, "ETHUSDT", Timeframe.H1, settings)
        # bar terakhir tumpang tindih + 6 bar baru dengan harga beda
        new_tail = make_df("2026-08-01T23:00", 7, base_price=200.0)
        total = upsert_ohlcv(new_tail, "ETHUSDT", Timeframe.H1, settings)
        merged = load_ohlcv("ETHUSDT", Timeframe.H1, settings)
        assert len(merged) == total == 30  # 24 + 6 baru, 1 overlap replace
        assert float(merged["close"].iloc[-1]) == 200.0  # versi baru menang
        assert merged.index.is_monotonic_increasing

    def test_ohlcv_path_layout(self, settings):
        p = ohlcv_path(settings, "btcusdt", Timeframe.M15)
        assert p == settings.data_dir / "parquet" / "BTCUSDT" / "15m.parquet"

    def test_sqlite_run_lifecycle(self, settings):
        init_db(settings)
        run_id = start_run("BTCUSDT", Timeframe.H1, "binance", settings)
        finish_run(run_id, rows_written=42, settings=settings)
        import sqlite3

        with sqlite3.connect(settings.db_path) as conn:
            row = conn.execute(
                "SELECT status, rows_written FROM ingestion_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row == ("ok", 42)

    def test_sqlite_failure_recorded(self, settings):
        init_db(settings)
        run_id = start_run("NVDA", Timeframe.H1, "yfinance", settings)
        finish_run(run_id, 0, error="boom", settings=settings)
        import sqlite3

        with sqlite3.connect(settings.db_path) as conn:
            row = conn.execute(
                "SELECT status, error FROM ingestion_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row[0] == "failed"
        assert row[1] == "boom"


class TestQuality:
    def test_empty_frame_finding(self):
        findings = run_checks(pd.DataFrame(), "binance")
        assert findings[0]["check_name"] == "empty_frame"

    def test_clean_crypto_series_no_warns(self):
        df = make_df("2026-08-01", 48)
        df.attrs["timeframe"] = "1h"
        findings = run_checks(df, "binance")
        warns = [f for f in findings if f["severity"] == "warn"]
        assert warns == []

    def test_gap_detected_for_crypto(self):
        df = make_df("2026-08-01", 48)
        df = df.drop(df.index[10:20])  # gap 10 jam
        df.attrs["timeframe"] = "1h"
        findings = run_checks(df, "binance")
        gaps = next(f for f in findings if f["check_name"] == "cadence_gaps")
        assert gaps["severity"] == "warn"

    def test_outlier_flagged(self):
        df = make_df("2026-08-01", 24)
        idx = df.index[5]
        df.loc[idx, "close"] = df.loc[idx, "close"] * 1.9  # lompatan 90%
        df.attrs["timeframe"] = "1h"
        findings = run_checks(df, "yfinance")
        assert any(f["check_name"] == "outlier_returns_gt_20pct" for f in findings)


class TestRouting:
    @pytest.mark.parametrize(
        ("ticker", "expected"),
        [
            ("BTCUSDT", "binance"),
            ("ethusdt", "binance"),
            ("EUR_USD", "oanda"),
            ("GBP_USD", "oanda"),
            ("NVDA", "yfinance"),
            ("AAPL", "yfinance"),
        ],
    )
    def test_detect_source(self, ticker, expected):
        assert detect_source(ticker) == expected

    @pytest.mark.parametrize(
        ("ticker", "symbol"),
        [("BTCUSDT", "BTC/USDT"), ("ETHUSDC", "ETH/USDC"), ("TONUSDT", "TON/USDT")],
    )
    def test_ccxt_symbol(self, ticker, symbol):
        assert to_ccxt_symbol(ticker) == symbol

    def test_ccxt_symbol_unknown_rejected(self):
        with pytest.raises(ValueError):
            to_ccxt_symbol("WEIRD")


def test_timeframe_roundtrip_values():
    assert Timeframe("1h") is Timeframe.H1
    assert Timeframe("1d").value == "1d"


def test_utc_now_is_aware():
    from seith_core.schemas import utcnow

    assert utcnow().tzinfo is not None

class TestReviewFixes:
    """Regression tests dari temuan review gate P1."""

    def test_store_rejects_naive_index(self, settings):
        df = make_df("2026-08-01", 5)
        df.index = df.index.tz_localize(None)
        with pytest.raises(ValueError, match="tz-aware"):
            save_ohlcv(df, "BTCUSDT", Timeframe.H1, settings)

    def test_store_rejects_wrong_columns(self, settings):
        df = make_df("2026-08-01", 5).drop(columns=["volume"])
        with pytest.raises(ValueError, match="kolom"):
            save_ohlcv(df, "BTCUSDT", Timeframe.H1, settings)

    def test_store_rejects_path_traversal_ticker(self, settings):
        df = make_df("2026-08-01", 5)
        with pytest.raises(ValidationError):
            save_ohlcv(df, "../evil", Timeframe.H1, settings)

    def test_load_injects_timeframe_attr(self, settings):
        df = make_df("2026-08-01", 5)
        save_ohlcv(df, "BTCUSDT", Timeframe.H1, settings)
        loaded = load_ohlcv("BTCUSDT", Timeframe.H1, settings)
        assert loaded.attrs["timeframe"] == "1h"

    def test_yf_fetch_never_sends_end_kwarg(self, monkeypatch):
        import seith_data.sources.yf as yf_mod

        captured = {}

        def fake_download(*args, **kwargs):
            captured.update(kwargs)
            return make_df("2026-08-01", 48)

        monkeypatch.setattr(yf_mod.yf, "download", fake_download)
        start = datetime(2026, 8, 1, tzinfo=UTC)
        end = datetime(2026, 8, 3, tzinfo=UTC)
        yf_mod.fetch("NVDA", Timeframe.H1, start, end)
        assert "end" not in captured

    def test_future_step_uses_canonical_timeframe_not_tail_gap(self):
        from seith_data.timeutil import timeframe_seconds

        step = pd.Timedelta(seconds=timeframe_seconds(Timeframe.H1))
        # ekor data ber-gap (Jumat -> Senin) TIDAK boleh mempengaruhi horizon
        tail_gap = pd.Timedelta(hours=66)
        y_ts = pd.date_range(
            pd.Timestamp("2026-08-21 22:00", tz="UTC") + tail_gap,
            periods=3,
            freq=step,
        )
        diffs = pd.Series(y_ts).diff().dropna().unique()
        assert list(diffs) == [pd.Timedelta(hours=1)]

    def test_naive_start_rejected_by_binance_guard(self):
        from seith_data.sources import binance

        naive = datetime(2026, 8, 1)  # tanpa tz
        with pytest.raises(ValueError, match="timezone-aware"):
            binance.fetch("BTCUSDT", Timeframe.H1, naive, datetime.now(UTC))
