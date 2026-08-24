"""Test GATE-A harness: walk-forward integrity, simulasi trade, agregasi."""

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest
from seith_core.schemas import AssetClass, EconomicEvent, EventImportance

from seith_analysis.backtest_news import (
    BucketStat,
    NewsTradeResult,
    _month_starts,
    _next_month,
    run_walk_forward,
    simulate_trade,
)

REL = datetime(2026, 9, 10, 12, 30, tzinfo=UTC)


def make_m1_around(release: datetime, minutes_before: int, minutes_after: int,
                   post_close: float, ref_close: float = 100.0) -> pd.DataFrame:
    """Grid kontinu: close ref sampai rilis, lalu post_close setelahnya."""
    start = release - pd.Timedelta(minutes=minutes_before)
    idx = pd.date_range(start, periods=minutes_before + minutes_after, freq="1min",
                        tz="UTC")
    closes = np.full(len(idx), post_close)
    closes[: minutes_before + 1] = ref_close
    return pd.DataFrame(
        {"open": closes, "high": closes * 1.0001, "low": closes * 0.9999,
         "close": closes, "volume": 1.0},
        index=pd.DatetimeIndex(idx, name="timestamp"),
    ).astype("float64")


def make_event(when: datetime, etype: str = "jobless_claims",
               importance: EventImportance = EventImportance.MEDIUM,
               ticker: str = "BTCUSDT") -> EconomicEvent:
    return EconomicEvent(
        source_ref=f"fred:t:{when.isoformat()}:{ticker}",
        source="fred", ticker=ticker, asset_class=AssetClass.CRYPTO,
        event_type=etype, importance=importance, currency="USD",
        scheduled_at=when, actual=1.0, forecast=1.0,
    )


class TestSimulateTrade:
    def _frame(self):
        # 11 bar pra-rilis close=100 (TERMASUK bar rilis), lalu naik ke 101
        pre = np.full(11, 100.0)
        post = np.full(19, 101.0)
        closes = np.concatenate([pre, post])
        idx = pd.date_range(REL - pd.Timedelta(minutes=10), periods=len(closes),
                            freq="1min", tz="UTC")
        return pd.DataFrame(
            {"open": closes, "high": closes * 1.0001, "low": closes * 0.9999,
             "close": closes, "volume": 1.0},
            index=pd.DatetimeIndex(idx, name="timestamp"),
        ).astype("float64")

    def test_entry_exit_and_cost(self):
        res = simulate_trade(self._frame(), REL, 15, +1, "BTCUSDT", "jobless_claims")
        assert res is not None
        assert res.entry_price == pytest.approx(100.0)
        assert res.exit_price == pytest.approx(101.0)
        assert res.gross_pct == pytest.approx(1.0)
        assert 0 < res.cost_pct < res.gross_pct  # biaya dipotong proporsional
        assert res.net_pct == pytest.approx(res.gross_pct - res.cost_pct)
        assert res.is_win is True

    def test_short_direction_inverts_gross(self):
        res = simulate_trade(self._frame(), REL, 15, -1, "BTCUSDT", "jobless_claims")
        assert res.gross_pct == pytest.approx(-1.0)
        assert res.is_win is False

    def test_grid_gap_returns_none(self):
        df = self._frame()
        holey = pd.concat([df.iloc[:12], df.iloc[13:]])  # satu menit bolong
        # gap membuat jarak exit != H menit -> trade DIBUANG (bukan exception)
        assert simulate_trade(holey.sort_index(), REL, 15, 1, "BTCUSDT", "x") is None

    def test_missing_release_bar_returns_none(self):
        df = self._frame()
        late_frame = df[df.index > REL + pd.Timedelta(minutes=30)]
        assert simulate_trade(late_frame, REL, 5, 1, "BTCUSDT", "x") is None

    def test_insufficient_forward_bars_none(self):
        df = self._frame().iloc[:12]
        assert simulate_trade(df, REL, 15, 1, "BTCUSDT", "x") is None


class TestBucketStat:
    def test_winrate_pf_drawdown(self):
        stat = BucketStat("BTCUSDT", "cpi", 15)
        for g in (1.0, -0.5, 1.0):  # net setelah biaya tetap positif utk 2 pertama
            stat.add(NewsTradeResult("BTCUSDT", "cpi", 15, REL, 1, 100, 100, g, 0.05))
        stat.finalize()
        assert stat.win_rate == pytest.approx(2 / 3)
        # profit factor memakai GROSS: wins 1.0+1.0=2.0, losses 0.5
        assert stat.profit_factor == pytest.approx(4.0)
        # net=[0.95,-0.55,0.95] -> cum [.95,.40,1.35]; worst=.40-.95=-0.55
        assert stat.max_drawdown_pct == pytest.approx(0.55)

    def test_drawdown_captured(self):
        stat = BucketStat("BTCUSDT", "cpi", 15)
        stat.add(NewsTradeResult("BTCUSDT", "cpi", 15, REL, 1, 100, 100, 1.0, 0.05))
        stat.add(NewsTradeResult("BTCUSDT", "cpi", 15, REL, 1, 100, 90, -10.0, 0.05))
        stat.finalize()
        assert stat.max_drawdown_pct == pytest.approx(10.05)


class TestMonthHelpers:
    def test_month_starts_span(self):
        starts = _month_starts(date(2026, 7, 15), date(2026, 9, 10))
        assert starts == [
            datetime(2026, 7, 1).date(),
            datetime(2026, 8, 1).date(),
            datetime(2026, 9, 1).date(),
        ]

    def test_next_month_year_rollover(self):
        assert _next_month(datetime(2026, 12, 1).date()) == datetime(2027, 1, 1).date()


class TestWalkForward:
    def _dataset(self):
        """3 bulan: Jul-Agu seed (impuls NAIK konsisten), Sep uji (impuls TURUN)."""
        events: list[EconomicEvent] = []
        plan = []
        # seed: 12 event naik tersebar Jul-Agu (6/bulan)
        for month, days in ((7, (3, 8, 13, 18, 23, 28)), (8, (3, 8, 13, 18, 23, 28))):
            for d in days:
                plan.append((datetime(2026, month, d, 12, 30, tzinfo=UTC), 102.0))
        # uji: 4 event turun di Sep
        for d in (3, 10, 17, 24):
            plan.append((datetime(2026, 9, d, 12, 30, tzinfo=UTC), 98.0))

        pieces: list[pd.DataFrame] = []
        cursor = datetime(2026, 7, 1, tzinfo=UTC)
        for when, post in plan:
            gap = int((when - cursor).total_seconds() // 60) - 5
            filler_idx = pd.date_range(cursor, periods=max(gap, 1), freq="1min", tz="UTC")
            f = pd.DataFrame(
                {"open": 100.0, "high": 100.01, "low": 99.99, "close": 100.0,
                 "volume": 1.0},
                index=pd.DatetimeIndex(filler_idx, name="timestamp"),
            ).astype("float64")
            pieces.append(f)
            rel_idx = pd.date_range(when, periods=16, freq="1min", tz="UTC")
            closes = np.full(16, 100.0)
            closes[1:] = post  # bar rilis masih 100, sesudahnya menuju post
            rf = pd.DataFrame(
                {"open": closes, "high": closes + 0.02, "low": closes - 0.02,
                 "close": closes, "volume": 1.0},
                index=pd.DatetimeIndex(rel_idx, name="timestamp"),
            ).astype("float64")
            pieces.append(rf)
            events.append(make_event(when))
            cursor = when + pd.Timedelta(minutes=16)
        tail_idx = pd.date_range(cursor, periods=60 * 24 * 20, freq="1min", tz="UTC")
        pieces.append(pd.DataFrame(
            {"open": 100.0, "high": 100.01, "low": 99.99, "close": 100.0, "volume": 1.0},
            index=pd.DatetimeIndex(tail_idx, name="timestamp"),
        ).astype("float64"))
        m1 = pd.concat(pieces)
        m1 = m1[~m1.index.duplicated()].sort_index()
        return events, m1

    def test_trades_only_after_seed_and_past_only_library(self, monkeypatch):
        events, m1 = self._dataset()
        captured_train_max: list[datetime] = []

        import seith_analysis.pattern_library as pl

        real_build = pl.build_pattern_library

        def spy(events_arg, loader, **kw):
            mx = max(e.scheduled_at for e in events_arg)
            captured_train_max.append(mx)
            return real_build(events_arg, loader, **kw)

        monkeypatch.setattr(pl, "build_pattern_library", spy)

        report = run_walk_forward(
            events, lambda t: m1,
            horizons=(15,), seed_months=2, min_samples=5,
            min_continuation_prob=0.55,
        )

        assert report.n_months_tested == 1  # hanya September
        assert report.n_trades_total >= 4   # semua event Sep lolos gate pola kuat
        # INVARIANT ANTI-LEAK: setiap library hanya dari event SEBELUM bulan uji
        for train_max in captured_train_max:
            assert train_max.month in (7, 8)
        # arah trade mengikuti pola masa lalu (NAIK) meski September turun ->
        # kalau bocor masa depan, prob_up akan tergerus dan arah bisa berubah
        assert all(s.total_net_pct < 0 for s in report.stats)  # short-bias salah? tidak:
        # arah BUY di pasar yang turun -> net negatif memang diharapkan di sini;
        # yang penting SEMUA trade terjadi dan arahnya konsisten past-only.
