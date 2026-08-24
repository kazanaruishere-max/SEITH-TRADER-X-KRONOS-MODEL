"""Test E3-wiring + gate E4: news bridge -> proposal lewat money-path.

Skenario kunci (Tier-0):
- OFF: tidak ada proposal dibuat
- SEMI: proposal PENDING_APPROVAL (menunggu manusia)
- AUTO conf>=threshold: langsung APPROVED approved_by='system:auto'
- AUTO conf<threshold: turun ke SEMI-flow
- RiskManager TIDAK dilewati: bridge hanya membuat proposal; eksekusi tetap intake.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from seith_core.config import AppSettings
from seith_core.schemas import AssetClass, EconomicEvent, EventImportance
from seith_data.events_store import upsert_economic_events
from seith_data.trading_mode import TradingMode, set_trading_mode

from seith_trader import proposals
from seith_trader.news_bridge import (
    _sanitize_for_channel,
    load_pattern_library,
    process_signals_once,
)


@pytest.fixture()
def settings(tmp_path):
    return AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "test.db",
    )


NOW = datetime(2026, 9, 4, 12, 40, tzinfo=UTC)  # 10 menit pasca-rilis


def make_event(**overrides) -> EconomicEvent:
    defaults = dict(
        source_ref="fred:180:test",
        source="fred",
        ticker="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        event_type="jobless_claims",
        importance=EventImportance.HIGH,
        currency="USD",
        scheduled_at=datetime(2026, 9, 4, 12, 30, tzinfo=UTC),
        actual=175000.0,
        forecast=185000.0,
    )
    defaults.update(overrides)
    return EconomicEvent(**defaults)


def write_library(settings, prob_cont=0.70) -> None:
    payload = {
        "patterns": [
            {
                "ticker": "BTCUSDT",
                "event_type": "jobless_claims",
                "horizon_minutes": 15,
                "sample_count": 20,
                "prob_initial_up": 0.80,
                "avg_spike_pct": 0.13,
                "p90_spike_pct": 0.23,
                "median_retracement_pct": 34.0,
                "prob_continuation": prob_cont,
                "prob_reversal": round(1 - prob_cont, 2),
            }
        ]
    }
    lib = settings.data_dir / "patterns" / "pattern_library.json"
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_text(json.dumps(payload), encoding="utf-8")


def seed_event(settings) -> None:
    """Bridge memuat event dari store - test wajib seed dulu."""
    upsert_economic_events([make_event()], settings=settings)


class TestNewsBridgeGating:
    def test_mode_off_creates_nothing(self, settings):
        set_trading_mode(TradingMode.OFF, updated_by="test", settings=settings)
        write_library(settings)
        created = process_signals_once(NOW, settings=settings,
                                       notify_fn=lambda t: None)
        assert created == []
        assert proposals.list_by_status(
            proposals.Status.PENDING_APPROVAL, settings
        ) == []

    def test_semi_creates_pending_and_notifies(self, settings):
        write_library(settings)
        seed_event(settings)
        notified: list[str] = []
        created = process_signals_once(NOW, settings=settings,
                                       notify_fn=notified.append)
        assert len(created) == 1
        p = proposals.load(created[0], settings)
        assert p is not None
        assert p.status.value == "pending_approval"  # SEMI: menunggu manusia
        assert p.approved_by is None
        assert p.signal_id.startswith("sig_")
        assert notified and "SINYAL NEWS" in notified[0]

    def test_auto_high_confidence_approves_as_system(self, settings):
        set_trading_mode(TradingMode.AUTO, updated_by="test",
                         auto_min_confidence=0.50, settings=settings)
        write_library(settings, prob_cont=0.85)
        seed_event(settings)
        created = process_signals_once(NOW, settings=settings,
                                       notify_fn=lambda t: None)
        assert len(created) == 1
        p = proposals.load(created[0], settings)
        assert p is not None
        assert p.status.value == "approved"
        assert p.approved_by == "system:auto"

    def test_auto_low_confidence_falls_back_to_semi(self, settings):
        set_trading_mode(TradingMode.AUTO, updated_by="test",
                         auto_min_confidence=0.99, settings=settings)
        write_library(settings, prob_cont=0.85)
        seed_event(settings)
        created = process_signals_once(NOW, settings=settings,
                                       notify_fn=lambda t: None)
        p = proposals.load(created[0], settings)
        assert p is not None
        assert p.status.value == "pending_approval"

    def test_empty_library_creates_nothing(self, settings):
        # library file tidak ada -> tanpa pola -> trigger tidak menembak
        created = process_signals_once(NOW, settings=settings,
                                       notify_fn=lambda t: None)
        assert created == []

    def test_dedup_across_cycles_single_proposal(self, settings):
        """BLOCKER-fix regression: scan berulang atas rilis sama = 1 proposal."""
        write_library(settings)
        seed_event(settings)
        first = process_signals_once(NOW, settings=settings, notify_fn=lambda t: None)
        second = process_signals_once(NOW, settings=settings, notify_fn=lambda t: None)
        third = process_signals_once(
            NOW + timedelta(minutes=1), settings=settings, notify_fn=lambda t: None
        )
        assert len(first) == 1
        assert second == [first[0]]  # idempotent: return existing, bukan duplikat
        assert third == [first[0]]
        all_pending = proposals.list_by_status(
            proposals.Status.PENDING_APPROVAL, settings
        )
        assert len(all_pending) == 1

    def test_no_signal_when_event_outside_window(self, settings):
        write_library(settings)
        seed_event(settings)
        late = NOW + timedelta(hours=2)  # di luar window 30m trigger
        created = process_signals_once(late, settings=settings,
                                       notify_fn=lambda t: None)
        assert created == []


class TestBridgeHelpers:
    def test_load_pattern_library_skips_invalid_rows(self, settings):
        lib = settings.data_dir / "patterns" / "pattern_library.json"
        lib.parent.mkdir(parents=True, exist_ok=True)
        lib.write_text(json.dumps({
            "patterns": [
                {"bukan": "pattern"},  # invalid -> skip
                {
                    "ticker": "BTCUSDT", "event_type": "cpi", "horizon_minutes": 15,
                    "sample_count": 12, "prob_initial_up": 0.7, "avg_spike_pct": 0.5,
                    "p90_spike_pct": 1.0, "median_retracement_pct": 30.0,
                    "prob_continuation": 0.6, "prob_reversal": 0.4,
                },
            ]
        }), encoding="utf-8")
        patterns = load_pattern_library(lib)
        assert len(patterns) == 1
        assert patterns[0].ticker == "BTCUSDT"

    def test_sanitize_channel_hides_internal_ids(self):
        from seith_core.schemas import Action

        class FakeSig:
            action = Action.BUY
            ticker = "EUR_USD"
            confidence = 0.72

        text = _sanitize_for_channel(FakeSig())
        assert "LONG EUR_USD" in text
        assert "proposal" not in text.lower()
