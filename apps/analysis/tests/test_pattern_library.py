"""Test E2 OTAK: pattern library anti-lookahead + model spread melebar news."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from seith_core.schemas import AssetClass, EconomicEvent, EventImportance

from seith_analysis.news_spread import (
    effective_spread_bps,
    round_trip_cost_bps,
    widened_spread_multiplier,
)
from seith_analysis.pattern_library import (
    build_pattern_library,
    measure_release_window,
)

RELEASE = datetime(2026, 9, 4, 12, 30, tzinfo=UTC)


def make_m1(rows: list[tuple[float, float, float]], start: str = "2026-09-04T12:20:00Z"):
    """rows: (high, low, close) per menit mulai `start`."""
    idx = pd.date_range(start, periods=len(rows), freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "open": [r[2] for r in rows],
            "high": [r[0] for r in rows],
            "low": [r[1] for r in rows],
            "close": [r[2] for r in rows],
            "volume": 1.0,
        },
        index=pd.DatetimeIndex(idx, name="timestamp"),
    ).astype("float64")


def make_event(event_id: str = "evt_test", **overrides) -> EconomicEvent:
    defaults = dict(
        source_ref=f"finnhub:{event_id}",
        source="finnhub",
        ticker="EUR_USD",
        asset_class=AssetClass.FOREX,
        event_type="non_farm_payrolls",
        importance=EventImportance.HIGH,
        currency="USD",
        scheduled_at=RELEASE,
    )
    defaults.update(overrides)
    return EconomicEvent(**defaults)


class TestAntiLookahead:
    def test_reference_is_last_bar_strictly_before_release(self):
        # 9 bar pra-rilis (12:20..12:28) lalu bar referensi 12:29 close=100,
        # rilis di 12:30 -> referensi wajib 100.0, BUKAN 101.0 (bar rilis)
        rows = [(99.5, 98.5, 99.0)] * 9 + [(100.5, 99.5, 100.0)]
        rows += [(102.0, 99.0, 101.0)] * 10  # window rilis mulai 12:30
        m1 = make_m1(rows)
        metrics = measure_release_window(
            m1, RELEASE, "evt_x", "EUR_USD", "non_farm_payrolls", horizons=(5,)
        )
        assert len(metrics) == 1
        assert metrics[0].reference_price == pytest.approx(100.0)

    def test_release_before_all_data_skipped(self):
        m1 = make_m1([(100.0, 99.0, 100.0)] * 20)
        metrics = measure_release_window(
            m1, datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
            "evt_y", "EUR_USD", "cpi_us", horizons=(5,),
        )
        assert metrics == ()

    def test_naive_release_datetime_rejected(self):
        m1 = make_m1([(100.0, 99.0, 100.0)] * 20)
        with pytest.raises(ValueError, match="timezone-aware"):
            measure_release_window(
                m1, RELEASE.replace(tzinfo=None),
                "evt_z", "EUR_USD", "cpi_us", horizons=(5,),
            )

    def test_incomplete_window_horizon_dropped(self):
        # hanya 3 bar setelah rilis -> horizon 5m tidak lengkap, dibuang semua
        rows = [(100.5, 99.5, 100.0)] * 10
        rows += [(102.0, 99.0, 101.0)] * 3
        m1 = make_m1(rows)
        metrics = measure_release_window(
            m1, RELEASE, "evt_w", "EUR_USD", "non_farm_payrolls", horizons=(5,)
        )
        assert metrics == ()

    def test_metrics_use_only_window_bars(self):
        # spike besar TERJADI SETELAH akhir window 5m tidak boleh terbaca;
        # window 5m: 12:30..12:34, bar 12:35 dst punya high ekstrem
        rows = [(100.5, 99.5, 100.0)] * 10
        rows += [(101.0, 99.5, 100.5)] * 5      # window 5m tenang
        rows += [(120.0, 90.0, 100.0)] * 250    # chaos sesudahnya
        m1 = make_m1(rows)
        metrics = measure_release_window(
            m1, RELEASE, "evt_q", "EUR_USD", "non_farm_payrolls", horizons=(5, 15)
        )
        five = next(m for m in metrics if m.horizon_minutes == 5)
        assert five.spike_size_pct <= 1.5  # tidak termakan spike di luar window

    def test_gap_duplicate_compensation_rejected(self):
        # duplikasi timestamp + gap yang saling mengompensasi: len==minutes
        # tercapai, tapi grid interior tidak kontinyu -> horizon wajib dibuang
        base = make_m1([(101.0, 99.0, 100.5)] * 16)
        idx = list(base.index)
        idx[11] = idx[10]  # duplikat di posisi 11 -> gap terselubung setelahnya
        tampered = pd.DataFrame(
            base.iloc[: len(idx)].values,
            index=pd.DatetimeIndex(idx),
            columns=base.columns,
        )
        metrics = measure_release_window(
            tampered, RELEASE, "evt_dup", "EUR_USD", "non_farm_payrolls",
            horizons=(5,),
        )
        assert metrics == ()

    def test_non_monotonic_index_rejected(self):
        base = make_m1([(101.0, 99.0, 100.5)] * 15)
        shuffled = pd.concat([base.iloc[5:10], base.iloc[0:5]])
        with pytest.raises(ValueError, match="monotonik"):
            measure_release_window(
                shuffled, RELEASE, "evt_s", "EUR_USD", "non_farm_payrolls",
                horizons=(5,),
            )

    def test_normal_series_still_accepted(self):
        base = make_m1([(101.0, 99.0, 100.5)] * 15)
        assert base.index.is_monotonic_increasing
        metrics = measure_release_window(
            base, RELEASE, "evt_ok", "EUR_USD", "non_farm_payrolls", horizons=(5,)
        )
        assert len(metrics) == 1


class TestLoaderCache:
    def test_loader_called_once_per_ticker(self):
        calls: list[str] = []

        def loader(ticker):
            calls.append(ticker)
            rows = [(100.5, 99.5, 100.0)] * 10
            rows += [(102.0, 99.0, 101.5)] * 5
            return make_m1(rows)

        events = [
            make_event(f"evt_{i}", scheduled_at=RELEASE) for i in range(3)
        ]
        # tiga event natural-key identik -> agregat hanya 1 sampel valid,
        # tapi loader WAJIB dipanggil tepat 1x (cache benar, bukan setdefault eager)
        build_pattern_library(events, loader, horizons=(5,))
        assert len(calls) == 1


class TestMetricMath:
    def _m1_with_path(self, post_close: float) -> pd.DataFrame:
        rows = [(100.5, 99.5, 100.0)] * 10          # pra-rilis, ref=100
        rows.append((102.0, 99.0, 101.0))           # bar rilis: spike atas 2%
        rows += [(101.8, 100.8, post_close)] * 4    # sisa window
        return make_m1(rows)

    def test_continuation(self):
        metrics = measure_release_window(
            self._m1_with_path(101.5), RELEASE,
            "evt_a", "EUR_USD", "non_farm_payrolls", horizons=(5,),
        )
        m = metrics[0]
        assert m.spike_size_pct == pytest.approx(2.0)
        assert m.initial_direction == 1
        assert m.move_at_horizon_pct == pytest.approx(1.5)
        assert m.retracement_pct == pytest.approx(25.0)  # (102-101.5)/2
        assert m.continued is True and m.is_reversed is False

    def test_reversal_when_more_than_half_retraced(self):
        metrics = measure_release_window(
            self._m1_with_path(100.5), RELEASE,
            "evt_b", "EUR_USD", "non_farm_payrolls", horizons=(5,),
        )
        m = metrics[0]
        assert m.retracement_pct == pytest.approx(75.0)
        assert m.is_reversed is True and m.continued is False

    def test_downward_impulse_direction_minus_one(self):
        rows = [(100.5, 99.5, 100.0)] * 10
        rows.append((100.2, 97.0, 98.0))         # spike bawah 3%
        rows += [(98.2, 97.6, 98.1)] * 4
        m1 = make_m1(rows)
        (m,) = measure_release_window(
            m1, RELEASE, "evt_c", "EUR_USD", "non_farm_payrolls", horizons=(5,)
        )
        assert m.initial_direction == -1
        assert m.spike_size_pct == pytest.approx(3.0)
        assert m.retracement_pct == pytest.approx((98.1 - 97.0) / 3.0 * 100.0)


class TestPatternAggregation:
    def test_build_library_groups_and_probabilities(self):
        # deret 12:20..13:49 (90 bar); dua rilis identik di 12:30 (pos 10) dan
        # 13:30 (pos 70), keduanya window 5 bar spike atas -> 2 sampel valid
        n = 90
        rows = [(100.5, 99.5, 100.0)] * n
        rows = list(rows)
        for pos in (10, 11, 12, 13, 14, 70, 71, 72, 73, 74):
            rows[pos] = (102.0, 99.0, 101.5)

        def loader(ticker):
            if ticker != "EUR_USD":
                return None
            return make_m1(rows)

        events = [
            make_event("evt_1"),
            make_event("evt_2", scheduled_at=datetime(2026, 9, 4, 13, 30, tzinfo=UTC)),
            make_event("evt_3", ticker="NVDA", asset_class=AssetClass.EQUITY_US),
        ]
        summaries = build_pattern_library(events, loader, horizons=(5,))
        nfp = [s for s in summaries if s.event_type == "non_farm_payrolls"]
        assert len(nfp) == 1
        s = nfp[0]
        assert s.ticker == "EUR_USD"
        assert s.sample_count == 2
        assert s.prob_initial_up == pytest.approx(1.0)
        assert s.prob_continuation == pytest.approx(1.0)
        assert s.prob_reversal == pytest.approx(0.0)
        assert np.isfinite(s.p90_spike_pct)

    def test_missing_m1_ticker_skipped_not_crash(self):
        summaries = build_pattern_library(
            [make_event("evt_missing")], lambda _: None, horizons=(5,)
        )
        assert summaries == ()


class TestNewsSpreadModel:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (-60.0, 8.0),      # 1 menit sebelum: dalam tier paling parah
            (0.0, 8.0),        # saat rilis
            (600.0, 8.0),      # tepat +10 menit: batas tier inklusif
            (-120.0, 8.0),     # tepat -2 menit: batas tier inklusif
            (-121.0, 3.0),     # keluar tier 1 -> tier 2
            (601.0, 3.0),      # keluar tier 1 -> tier 2
            (1800.0, 3.0),     # +30 menit masih tier 2
            (1801.0, 1.0),     # bebas widening
            (-301.0, 1.0),     # > 5 menit sebelum: normal
        ],
    )
    def test_multiplier_tiers(self, seconds, expected):
        assert widened_spread_multiplier(seconds) == expected

    def test_effective_spread_bps(self):
        assert effective_spread_bps(10.0, -60.0) == pytest.approx(80.0)
        assert effective_spread_bps(10.0, -3600.0) == pytest.approx(10.0)

    def test_round_trip_cost_combines_entry_exit(self):
        cost = round_trip_cost_bps(10.0, entry_seconds_from_release=-60.0,
                                   exit_seconds_from_release=900.0)
        assert cost == pytest.approx(80.0 + 30.0)

    def test_negative_base_spread_rejected(self):
        with pytest.raises(ValueError, match="non-negatif"):
            effective_spread_bps(-1.0, 0.0)

    def test_non_finite_offset_rejected(self):
        with pytest.raises(ValueError, match="finite"):
            widened_spread_multiplier(float("nan"))
        with pytest.raises(ValueError, match="finite"):
            round_trip_cost_bps(10.0, float("inf"), 0.0)
