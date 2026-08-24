"""Money-path tests: risk rules, state machine proposal, intake flow.

Semua murni lokal (tmp settings + DryRunSubmitter) - invariant Tier-0
dari skill seith-trading-safety diuji eksplisit di sini.
"""

from decimal import Decimal

import pytest
from seith_core.config import AppSettings
from seith_core.schemas import AssetClass, RiskLimits, Side
from seith_core.schemas import OrderProposalStatus as Status

from seith_trader import proposals, risk
from seith_trader.executor import DryRunSubmitter
from seith_trader.intake import halt_all_pending, process_pending


@pytest.fixture()
def settings(tmp_path):
    s = AppSettings(_env_file=None, data_dir=tmp_path / "d", db_path=tmp_path / "t.db")
    risk.init_risk_tables(s)
    proposals.init_tables(s)
    return s


def healthy_portfolio() -> risk.PortfolioState:
    return risk.PortfolioState(
        equity=Decimal("10000"),
        open_positions_count=0,
        daily_pnl=Decimal("0"),
        peak_equity=Decimal("10000"),
    )


def make_proposal(settings, **overrides):
    import uuid

    defaults = dict(
        # signal_id unik per panggilan: dedup UNIQUE(signal_id) sengaja menolak
        # duplikat sejak wiring E3 (idempotent bridge) - test butuh entitas beda
        signal_id=f"sig_test_{uuid.uuid4().hex[:12]}",
        ticker="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        side=Side.BUY,
        quantity=Decimal("0.05"),
    )
    defaults.update(overrides)
    return proposals.create_proposal(**defaults, settings=settings)


class TestRiskRules:
    def test_sizing_crypto_8dp(self):
        qty = risk.compute_order_quantity(
            Decimal("10000"),
            Decimal("50000"),
            AssetClass.CRYPTO,
            RiskLimits(max_position_pct=0.10),
        )
        assert qty == Decimal("0.02000000")  # 10% x 10k / 50k

    def test_sizing_equity_zero_safe(self):
        assert risk.compute_order_quantity(
            Decimal("0"), Decimal("100"), AssetClass.CRYPTO, RiskLimits()
        ) == Decimal("0")

    def test_evaluate_pass_sizes_down_to_limit(self, settings):
        p = make_proposal(settings, quantity=Decimal("1.0"))  # minta lebih dari limit
        d = risk.evaluate(
            p,
            healthy_portfolio(),
            mark_price=Decimal("50000"),
            limits=RiskLimits(max_position_pct=0.10),
            settings=settings,
        )
        assert d.approved and d.quantity == Decimal("0.02000000")

    def test_kill_switch_blocks_everything(self, settings):
        p = make_proposal(settings)
        risk.set_halt(True, settings)
        d = risk.evaluate(p, healthy_portfolio(), mark_price=Decimal("50000"), settings=settings)
        assert not d.approved and "kill switch" in d.reasons[0]
        risk.set_halt(False, settings)

    def test_drawdown_breaker_trips(self, settings):
        p = make_proposal(settings)
        portfolio = risk.PortfolioState(
            equity=Decimal("8900"),
            open_positions_count=0,
            daily_pnl=Decimal("0"),
            peak_equity=Decimal("10000"),
        )
        d = risk.evaluate(p, portfolio, mark_price=Decimal("50000"), settings=settings)
        assert not d.approved and any("drawdown" in r for r in d.reasons)

    def test_daily_loss_breaker_trips(self, settings):
        p = make_proposal(settings)
        portfolio = risk.PortfolioState(
            equity=Decimal("9800"),
            open_positions_count=0,
            daily_pnl=Decimal("-400"),
            peak_equity=Decimal("10000"),
        )
        d = risk.evaluate(p, portfolio, mark_price=Decimal("50000"), settings=settings)
        assert not d.approved and any("daily loss" in r for r in d.reasons)

    def test_max_open_positions_blocks_buy(self, settings):
        p = make_proposal(settings)
        portfolio = risk.PortfolioState(
            equity=Decimal("10000"),
            open_positions_count=10,
            daily_pnl=Decimal("0"),
            peak_equity=Decimal("10000"),
        )
        d = risk.evaluate(p, portfolio, mark_price=Decimal("50000"), settings=settings)
        assert not d.approved and any("posisi terbuka" in r for r in d.reasons)


class TestProposalStateMachine:
    def test_legal_flow_pending_approved_submitted_filled(self, settings):
        p = make_proposal(settings)
        p2 = proposals.approve(p.proposal_id, approved_by="telegram:6595275429", settings=settings)
        assert p2.status is Status.APPROVED and p2.approved_by == "telegram:6595275429"
        p3 = proposals.transition(
            p2.proposal_id, Status.SUBMITTED, approved_by="telegram:6595275429", settings=settings
        )
        p4 = proposals.transition(p3.proposal_id, Status.FILLED, settings=settings)
        assert p4.status is Status.FILLED

    def test_illegal_transition_rejected(self, settings):
        p = make_proposal(settings)  # masih PENDING
        with pytest.raises(ValueError, match="ilegal"):
            proposals.transition(p.proposal_id, Status.FILLED, settings=settings)

    def test_approve_without_approver_rejected(self, settings):
        p = make_proposal(settings)
        with pytest.raises(ValueError, match="approved_by"):
            proposals.transition(p.proposal_id, Status.APPROVED, settings=settings)

    def test_reject_flow(self, settings):
        p = make_proposal(settings)
        out = proposals.reject(p.proposal_id, reason="user menolak", settings=settings)
        assert out.status is Status.REJECTED

    def test_unknown_proposal_keyerror(self, settings):
        assert proposals.load("dec_tidakada", settings) is None
        with pytest.raises(KeyError):
            proposals.transition("dec_tidakada", Status.CANCELLED, settings=settings)

    def test_ticker_validated_on_create(self, settings):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            make_proposal(settings, ticker="../evil")


class TestIntakeFlow:
    def _mark(self, proposal):
        return Decimal("50000")

    def test_full_happy_path(self, settings):
        p = make_proposal(settings)
        proposals.approve(p.proposal_id, approved_by="telegram:6595275429", settings=settings)
        submitter = DryRunSubmitter()
        results = process_pending(self._mark, healthy_portfolio(), submitter, settings=settings)
        assert len(results) == 1 and results[0].action == "submitted"
        assert submitter.submissions[0][2] == "0.02000000"  # sizing turun ke limit
        final = proposals.load(p.proposal_id, settings)
        assert final.status is Status.SUBMITTED

    def test_risk_rejection_cancels_with_reason(self, settings):
        p = make_proposal(settings)
        proposals.approve(p.proposal_id, approved_by="telegram:6595275429", settings=settings)
        risk.set_halt(True, settings)
        try:
            results = process_pending(
                self._mark, healthy_portfolio(), DryRunSubmitter(), settings=settings
            )
        finally:
            risk.set_halt(False, settings)
        assert results[0].action == "risk_rejected"
        assert proposals.load(p.proposal_id, settings).status is Status.CANCELLED

    def test_submit_failure_cancels(self, settings):
        p = make_proposal(settings)
        proposals.approve(p.proposal_id, approved_by="telegram:6595275429", settings=settings)
        submitter = DryRunSubmitter(fail_next=True)
        results = process_pending(self._mark, healthy_portfolio(), submitter, settings=settings)
        assert results[0].action == "error"
        assert proposals.load(p.proposal_id, settings).status is Status.CANCELLED

    def test_halt_cancels_all_open(self, settings):
        p1 = make_proposal(settings)
        p2 = make_proposal(settings)
        proposals.approve(p1.proposal_id, approved_by="op", settings=settings)
        cancelled = halt_all_pending(settings=settings)
        assert cancelled == 2  # 1 APPROVED + 1 PENDING
        assert proposals.load(p1.proposal_id, settings).status is Status.CANCELLED
        assert proposals.load(p2.proposal_id, settings).status is Status.CANCELLED
