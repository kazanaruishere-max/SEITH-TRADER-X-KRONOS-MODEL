"""Round-trip & validation tests untuk domain schemas.

Kontrak antar-service adalah serialisasi JSON: model_dump_json() -> model_validate_json()
harus menghasilkan objek yang identik.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from seith_core.config import AppSettings, detect_unknown_seith_env_vars
from seith_core.schemas import (
    Action,
    AgentReport,
    AssetClass,
    Decision,
    ForecastResult,
    OrderProposal,
    OrderProposalStatus,
    PositionSnapshot,
    Side,
    Signal,
    SignalSource,
    Timeframe,
    action_to_side,
    new_id,
)

AWARE_TS = datetime(2026, 8, 23, 10, 0, 0, tzinfo=UTC)
NAIVE_TS = "2026-08-23T10:00:00"  # tanpa offset -> wajib ditolak


class TestSignal:
    def make(self, **overrides) -> Signal:
        defaults = dict(
            ticker="btcusdt",
            asset_class=AssetClass.CRYPTO,
            action=Action.BUY,
            confidence=0.82,
            source=SignalSource.TRADING_AGENTS,
            rationale="Bullish debate outcome",
        )
        defaults.update(overrides)
        return Signal(**defaults)

    def test_json_round_trip(self):
        signal = self.make()
        restored = Signal.model_validate_json(signal.model_dump_json())
        assert restored == signal

    def test_ticker_normalized_to_upper(self):
        assert self.make().ticker == "BTCUSDT"

    def test_ticker_rejects_invalid_chars(self):
        with pytest.raises(ValidationError):
            self.make(ticker="BTC USDT!")
        with pytest.raises(ValidationError):
            self.make(ticker="")

    def test_empty_ticker_rejected(self):
        with pytest.raises(ValidationError):
            self.make(ticker="   ")

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            self.make(confidence=1.5)
        with pytest.raises(ValidationError):
            self.make(confidence=-0.1)

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValidationError):
            self.make(created_at=NAIVE_TS)

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            self.make(unknown_field="typo magnet")


class TestDecision:
    def make(self, **overrides) -> Decision:
        defaults = dict(
            ticker="NVDA",
            asset_class=AssetClass.EQUITY_US,
            trade_date="2026-08-23",
            action=Action.BUY,
            confidence=0.7,
            reasoning_summary="Analysts bullish, risk acceptable",
            reports=(
                AgentReport(agent_name="technical", role="analyst", content="RSI oversold"),
                AgentReport(agent_name="bull", role="researcher", content="Strong thesis"),
            ),
            risk_assessment="Within limits",
        )
        defaults.update(overrides)
        return Decision(**defaults)

    def test_json_round_trip_with_reports(self):
        decision = self.make()
        restored = Decision.model_validate_json(decision.model_dump_json())
        assert restored == decision
        assert len(restored.reports) == 2

    def test_trade_date_parsed_as_date(self):
        assert self.make().trade_date == date(2026, 8, 23)

    def test_trade_date_invalid_format_rejected(self):
        with pytest.raises(ValidationError):
            self.make(trade_date="23 Aug 2026")

    def test_ticker_shared_normalization(self):
        assert self.make(ticker="nvda ").ticker == "NVDA"

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValidationError):
            self.make(created_at=NAIVE_TS)


class TestOrderProposal:
    def make(self, **overrides) -> OrderProposal:
        defaults = dict(
            signal_id="sig_abc123",
            ticker="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            side=Side.BUY,
            quantity=Decimal("0.5"),
        )
        defaults.update(overrides)
        return OrderProposal(**defaults)

    def test_json_round_trip_preserves_decimal(self):
        proposal = self.make()
        restored = OrderProposal.model_validate_json(proposal.model_dump_json())
        assert restored == proposal
        assert restored.quantity == Decimal("0.5")

    def test_default_status_pending(self):
        assert self.make().status == OrderProposalStatus.PENDING_APPROVAL

    def test_zero_quantity_rejected(self):
        with pytest.raises(ValidationError):
            self.make(quantity=Decimal("0"))

    def test_market_order_rejects_limit_price(self):
        with pytest.raises(ValidationError):
            self.make(order_type="market", limit_price=Decimal("50000"))

    def test_limit_order_requires_limit_price(self):
        with pytest.raises(ValidationError):
            self.make(order_type="limit")
        ok = self.make(order_type="limit", limit_price=Decimal("50000"))
        assert ok.limit_price == Decimal("50000")

    def test_unknown_order_type_rejected(self):
        with pytest.raises(ValidationError):
            self.make(order_type="mrket")

    # --- Invariant approval gate (CRITICAL C-1) ---

    def test_approved_without_approver_rejected(self):
        with pytest.raises(ValidationError, match="approved_by"):
            self.make(status="approved")

    def test_submitted_without_approver_rejected(self):
        with pytest.raises(ValidationError, match="approved_by"):
            self.make(status="submitted")

    def test_filled_without_approver_rejected(self):
        with pytest.raises(ValidationError, match="approved_by"):
            self.make(status="filled")

    def test_approved_with_approver_accepted(self):
        proposal = self.make(status="approved", approved_by="telegram:12345")
        assert proposal.approved_by == "telegram:12345"

    def test_pending_does_not_require_approver(self):
        assert self.make().approved_by is None

    def test_rejected_does_not_require_approver(self):
        self.make(status="rejected")


class TestForecastResult:
    def make(self, **overrides) -> ForecastResult:
        defaults = dict(
            ticker="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            timeframe="1h",
            horizon_bars=24,
            expected_return=0.012,
            confidence=0.65,
            ohlcv_path="BTCUSDT/1h/forecast.parquet",
        )
        defaults.update(overrides)
        return ForecastResult(**defaults)

    def test_json_round_trip(self):
        forecast = self.make()
        restored = ForecastResult.model_validate_json(forecast.model_dump_json())
        assert restored == forecast

    def test_horizon_must_be_positive(self):
        with pytest.raises(ValidationError):
            self.make(horizon_bars=0)

    def test_unknown_timeframe_rejected(self):
        with pytest.raises(ValidationError):
            self.make(timeframe="90m")

    def test_path_traversal_rejected(self):
        with pytest.raises(ValidationError):
            self.make(ohlcv_path="../etc/passwd")
        with pytest.raises(ValidationError):
            self.make(ohlcv_path="C:/Windows/system32/evil.parquet")

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValidationError):
            self.make(generated_at=NAIVE_TS)


class TestPositionSnapshot:
    def make(self, **overrides) -> PositionSnapshot:
        defaults = dict(
            ticker="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            side=Side.BUY,
            quantity=Decimal("0.5"),
            avg_price=Decimal("50000"),
            mark_price=Decimal("51000"),
            unrealized_pnl=Decimal("500"),
        )
        defaults.update(overrides)
        return PositionSnapshot(**defaults)

    def test_json_round_trip_preserves_decimals(self):
        snapshot = self.make()
        restored = PositionSnapshot.model_validate_json(snapshot.model_dump_json())
        assert restored == snapshot
        assert restored.quantity == Decimal("0.5")
        assert isinstance(restored.avg_price, Decimal)

    def test_computed_field_in_serialization(self):
        payload = self.make().model_dump_json()
        assert "unrealized_pnl_pct" in payload

    def test_pnl_pct_calculation(self):
        assert self.make().unrealized_pnl_pct == pytest.approx(2.0)

    def test_pnl_pct_zero_cost_safe(self):
        snapshot = self.make(avg_price=Decimal("0"))
        assert snapshot.unrealized_pnl_pct == 0.0

    def test_ticker_shared_normalization(self):
        assert self.make(ticker="eth-usdt").ticker == "ETH-USDT"


class TestSharedHelpers:
    def test_action_to_side(self):
        assert action_to_side(Action.BUY) == Side.BUY
        assert action_to_side(Action.SELL) == Side.SELL

    def test_action_to_side_hold_rejected(self):
        with pytest.raises(ValueError, match="HOLD"):
            action_to_side(Action.HOLD)

    def test_new_id_full_entropy(self):
        id_a, id_b = new_id("sig"), new_id("sig")
        assert id_a != id_b
        assert len(id_a.split("_", 1)[1]) == 32

    def test_timeframe_members_canonical(self):
        assert Timeframe.H1 == "1h"
        assert Timeframe.D1 == "1d"


class TestAppSettings:
    def test_defaults_without_env(self):
        settings = AppSettings(_env_file=None)
        assert settings.environment == "dev"
        assert settings.risk.max_drawdown_pct == 0.10
        assert settings.llm.api_key is None
        assert settings.kronos.device == "auto"

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("SEITH_ENVIRONMENT", "paper")
        monkeypatch.setenv("SEITH_RISK__MAX_DRAWDOWN_PCT", "0.15")
        settings = AppSettings(_env_file=None)
        assert settings.environment == "paper"
        assert settings.risk.max_drawdown_pct == 0.15

    def test_invalid_environment_value_rejected(self, monkeypatch):
        monkeypatch.setenv("SEITH_ENVIRONMENT", "bogus")
        with pytest.raises(ValidationError):
            AppSettings(_env_file=None)

    def test_unknown_seith_env_var_hard_fails(self, monkeypatch):
        monkeypatch.setenv("SEITH_BINANCE__API_SECRETT", "typo-secret")
        with pytest.raises(ValidationError):
            AppSettings(_env_file=None)
        assert "SEITH_BINANCE__API_SECRETT" in detect_unknown_seith_env_vars()

    def test_secret_not_leaked_in_repr(self, monkeypatch):
        monkeypatch.setenv("SEITH_LLM__API_KEY", "gsk_supersecret")
        settings = AppSettings(_env_file=None)
        assert "gsk_supersecret" not in repr(settings)
        assert settings.llm.api_key.get_secret_value() == "gsk_supersecret"

    def test_nested_settings_frozen(self):
        settings = AppSettings(_env_file=None)
        with pytest.raises(ValidationError):
            settings.llm.quick_model = "mutated"  # type: ignore[misc]

    # --- Guard kombinasi environment (HIGH H-1) ---

    def test_live_requires_binance_credentials(self, monkeypatch):
        monkeypatch.setenv("SEITH_ENVIRONMENT", "live")
        with pytest.raises(ValidationError, match="Binance"):
            AppSettings(_env_file=None)

    def test_live_with_credentials_accepted(self, monkeypatch):
        monkeypatch.setenv("SEITH_ENVIRONMENT", "live")
        monkeypatch.setenv("SEITH_BINANCE__API_KEY", "k")
        monkeypatch.setenv("SEITH_BINANCE__API_SECRET", "s")
        settings = AppSettings(_env_file=None)
        assert settings.environment == "live"

    def test_live_rejects_no_approval(self, monkeypatch):
        monkeypatch.setenv("SEITH_ENVIRONMENT", "live")
        monkeypatch.setenv("SEITH_BINANCE__API_KEY", "k")
        monkeypatch.setenv("SEITH_BINANCE__API_SECRET", "s")
        monkeypatch.setenv("SEITH_RISK__REQUIRE_APPROVAL", "false")
        with pytest.raises(ValidationError, match="require_approval"):
            AppSettings(_env_file=None)

    def test_paper_rejects_oanda_live(self, monkeypatch):
        monkeypatch.setenv("SEITH_ENVIRONMENT", "paper")
        monkeypatch.setenv("SEITH_OANDA__ENVIRONMENT", "live")
        with pytest.raises(ValidationError, match="oanda"):
            AppSettings(_env_file=None)

    def test_ensure_dirs_creates_db_parent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SEITH_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("SEITH_DB_PATH", str(tmp_path / "data" / "nested" / "s.db"))
        settings = AppSettings(_env_file=None)
        settings.ensure_dirs()
        assert (tmp_path / "data" / "parquet").is_dir()
        assert (tmp_path / "data" / "nested").is_dir()
