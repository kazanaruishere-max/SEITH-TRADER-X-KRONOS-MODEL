"""Test E3: news trigger engine - gate sebelum sinyal boleh jadi proposal.

Sinyal yang dihasilkan HANYA berisi arah+confidence; sizing/approval tetap
jalur existing (invariant Tier-0, tidak ada order di modul trigger).
"""

from datetime import UTC, datetime

from seith_core.news_trigger import TriggerConfig, evaluate_release, scan_releases
from seith_core.schemas import (
    Action,
    AssetClass,
    EconomicEvent,
    EventImportance,
    PatternSummary,
    SignalSource,
)

NOW = datetime(2026, 9, 4, 12, 40, tzinfo=UTC)  # 10 menit pasca-rilis


def make_event(event_id: str = "evt_default", **overrides) -> EconomicEvent:
    defaults = dict(
        event_id=event_id,
        source_ref=f"finnhub:{event_id}",
        source="finnhub",
        ticker="EUR_USD",
        asset_class=AssetClass.FOREX,
        event_type="non_farm_payrolls",
        importance=EventImportance.HIGH,
        currency="USD",
        scheduled_at=datetime(2026, 9, 4, 12, 30, tzinfo=UTC),
        actual=175000.0,
        forecast=185000.0,
    )
    defaults.update(overrides)
    return EconomicEvent(**defaults)


def make_pattern(**overrides) -> PatternSummary:
    defaults = dict(
        ticker="EUR_USD",
        event_type="non_farm_payrolls",
        horizon_minutes=15,
        sample_count=24,
        prob_initial_up=0.70,
        avg_spike_pct=0.8,
        p90_spike_pct=1.6,
        median_retracement_pct=20.0,
        prob_continuation=0.65,
        prob_reversal=0.35,
    )
    defaults.update(overrides)
    return PatternSummary(**defaults)


class TestEvaluateRelease:
    def test_happy_path_buy_on_up_pattern(self):
        signal = evaluate_release(make_event(), [make_pattern()], NOW)
        assert signal is not None
        assert signal.source is SignalSource.NEWS_EVENT
        assert signal.action is Action.BUY
        assert signal.ticker == "EUR_USD"
        assert 0.0 < signal.confidence < 1.0
        assert "surprise" in signal.rationale
        assert signal.strategy_hint.startswith("news:non_farm_payrolls@15m:")

    def test_sell_when_pattern_down_biased(self):
        pattern = make_pattern(prob_initial_up=0.25)
        signal = evaluate_release(make_event(), [pattern], NOW)
        assert signal is not None and signal.action is Action.SELL

    def test_unreleased_event_skipped(self):
        assert evaluate_release(make_event(actual=None), [make_pattern()], NOW) is None

    def test_zero_forecast_surprise_undefined_skipped(self):
        assert (
            evaluate_release(
                make_event(forecast=0.0), [make_pattern()], NOW
            )
            is None
        )

    def test_low_importance_below_threshold_skipped(self):
        cfg = TriggerConfig(min_importance=EventImportance.HIGH)
        assert (
            evaluate_release(
                make_event(importance=EventImportance.MEDIUM),
                [make_pattern()],
                NOW,
                config=cfg,
            )
            is None
        )

    def test_expired_window_skipped(self):
        late = datetime(2026, 9, 4, 13, 30, tzinfo=UTC)  # 60 menit > window 30m
        assert evaluate_release(make_event(), [make_pattern()], late) is None

    def test_future_timestamp_not_eligible(self):
        early = datetime(2026, 9, 4, 12, 29, tzinfo=UTC)  # sebelum rilis
        assert evaluate_release(make_event(), [make_pattern()], early) is None

    def test_no_matching_pattern_skipped(self):
        other = make_pattern(event_type="cpi_us")
        assert evaluate_release(make_event(), [other], NOW) is None

    def test_insufficient_samples_skipped(self):
        weak = make_pattern(sample_count=3)
        assert evaluate_release(make_event(), [weak], NOW) is None

    def test_low_continuation_prob_skipped(self):
        weak = make_pattern(prob_continuation=0.30)
        assert evaluate_release(make_event(), [weak], NOW) is None

    def test_neutral_pattern_no_edge_skipped(self):
        flat = make_pattern(prob_initial_up=0.5)
        assert evaluate_release(make_event(), [flat], NOW) is None

    def test_kronos_required_but_missing_blocks_signal(self):
        cfg = TriggerConfig(require_kronos_agree=True)
        assert evaluate_release(make_event(), [make_pattern()], NOW, config=cfg) is None

    def test_kronos_disagreement_blocks_signal(self):
        cfg = TriggerConfig(require_kronos_agree=True)
        signal = evaluate_release(
            make_event(), [make_pattern()], NOW, kronos_direction=-1, config=cfg
        )
        assert signal is None

    def test_kronos_agreement_allows_signal(self):
        cfg = TriggerConfig(require_kronos_agree=True)
        signal = evaluate_release(
            make_event(), [make_pattern()], NOW, kronos_direction=1, config=cfg
        )
        assert signal is not None
        assert "agree" in signal.rationale

    def test_confidence_capped_even_with_huge_surprise(self):
        huge = make_event(actual=10000.0)  # surprise raksasa
        signal = evaluate_release(huge, [make_pattern()], NOW)
        assert signal is not None
        assert signal.confidence <= 0.99

    def test_naive_now_rejected_fail_fast(self):
        # konsisten konvensi proyek: datetime naive ditolak keras, bukan diasumsikan
        import pytest as _pytest

        with _pytest.raises(ValueError):
            evaluate_release(
                make_event(), [make_pattern()], NOW.replace(tzinfo=None)
            )


class TestScanReleases:
    def test_scan_filters_to_valid_signals_only(self):
        events = [
            make_event("a"),
            make_event("b", actual=None),                       # belum rilis
            make_event("c", event_type="gdp_us"),               # tanpa pola cocok
            make_event("d", importance=EventImportance.LOW),    # di bawah threshold
        ]
        signals = scan_releases(events, [make_pattern()], NOW)
        assert len(signals) == 1
        assert signals[0].strategy_hint.endswith(":a")

    def test_kronos_directions_mapped_by_event_id(self):
        events = [make_event("a")]
        cfg = TriggerConfig(require_kronos_agree=True)
        blocked = scan_releases(events, [make_pattern()], NOW, config=cfg)
        assert blocked == []
        allowed = scan_releases(
            events, [make_pattern()], NOW,
            kronos_directions={"a": 1}, config=cfg,
        )
        assert len(allowed) == 1
