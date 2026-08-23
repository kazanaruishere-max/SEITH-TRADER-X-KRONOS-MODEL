"""Test rating_map + decision_store: pure logic tanpa panggilan LLM."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from seith_core.config import AppSettings
from seith_core.schemas import (
    Action,
    AgentReport,
    AssetClass,
    Decision,
    ForecastResult,
    Timeframe,
)

from seith_analysis.decision_store import (
    export_json,
    load_decision,
    load_recent,
    save_decision,
)
from seith_analysis.rating_map import (
    blend_confidence,
    kronos_agrees,
    map_rating_to_action,
)


class TestRatingMap:
    @pytest.mark.parametrize(
        ("rating", "expected"),
        [
            ("Buy", Action.BUY),
            ("OVERWEIGHT", Action.BUY),
            ("Hold", Action.HOLD),
            ("Underweight", Action.SELL),
            ("sell", Action.SELL),
        ],
    )
    def test_mapping(self, rating, expected):
        assert map_rating_to_action(rating) is expected

    def test_unknown_rating_rejected(self):
        with pytest.raises(ValueError, match="tidak dikenal"):
            map_rating_to_action("moon")

    def test_kronos_agreement_semantics(self):
        assert kronos_agrees(Action.BUY, 0.05) is True
        assert kronos_agrees(Action.SELL, -0.03) is True
        assert kronos_agrees(Action.BUY, -0.05) is False
        assert kronos_agrees(Action.HOLD, 0.10) is None
        assert kronos_agrees(Action.BUY, 0.0005) is None  # di bawah ambang

    def test_blend_bounds_and_direction(self):
        agree = blend_confidence(0.8, Action.BUY, 0.04)
        contradict = blend_confidence(0.8, Action.BUY, -0.04)
        assert agree > contradict
        for value in (agree, contradict):
            assert 0.05 <= value <= 0.95


def make_decision(**overrides) -> Decision:
    defaults = dict(
        ticker="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        trade_date=date(2026, 8, 23),
        action=Action.BUY,
        confidence=0.72,
        reasoning_summary="PM bullish",
        reports=(AgentReport(agent_name="kronos", role="forecast", content="ER +2%"),),
        risk_assessment="ok",
    )
    defaults.update(overrides)
    return Decision(**defaults)


class TestDecisionStore:
    def test_round_trip_by_id(self, tmp_path):
        settings = AppSettings(_env_file=None, data_dir=tmp_path, db_path=tmp_path / "t.db")
        decision = make_decision()
        save_decision(decision, settings)
        loaded = load_decision(decision.decision_id, settings)
        assert loaded == decision

    def test_upsert_same_id_replaces(self, tmp_path):
        settings = AppSettings(_env_file=None, data_dir=tmp_path, db_path=tmp_path / "t.db")
        d1 = make_decision(confidence=0.5)
        d2 = make_decision(decision_id=d1.decision_id, confidence=0.9)
        save_decision(d1, settings)
        save_decision(d2, settings)
        assert load_decision(d1.decision_id, settings).confidence == 0.9

    def test_load_missing_returns_none(self, tmp_path):
        settings = AppSettings(_env_file=None, data_dir=tmp_path, db_path=tmp_path / "t.db")
        assert load_decision("dec_tidakada", settings) is None

    def test_recent_listing_with_ticker_filter(self, tmp_path):
        settings = AppSettings(_env_file=None, data_dir=tmp_path, db_path=tmp_path / "t.db")
        save_decision(make_decision(), settings)
        save_decision(make_decision(ticker="NVDA", asset_class=AssetClass.EQUITY_US), settings)
        rows = load_recent("btcusdt", limit=5, settings=settings)
        assert len(rows) == 1 and rows[0]["ticker"] == "BTCUSDT"

    def test_forecast_result_still_valid_contract(self):
        fc = ForecastResult(
            ticker="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            timeframe=Timeframe.H1,
            horizon_bars=24,
            expected_return=0.01,
            confidence=0.7,
            ohlcv_path="parquet/BTCUSDT/forecast_1h.parquet",
        )
        restored = ForecastResult.model_validate_json(fc.model_dump_json())
        assert restored == fc

    def test_export_json_writes_file(self, tmp_path):
        decision = make_decision(created_at=datetime(2026, 8, 23, tzinfo=UTC))
        path = Path(export_json(decision, out_dir=tmp_path / "out"))
        assert "decision" in path.read_text(encoding="utf-8")

    def test_decision_requires_aware_timestamp(self):
        with pytest.raises(ValidationError):
            make_decision(created_at=datetime(2026, 8, 23))  # naive
