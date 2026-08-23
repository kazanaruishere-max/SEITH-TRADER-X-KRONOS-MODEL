"""Test auth + formatter - pure logic tanpa jaringan/aiogram runtime."""

import pytest
from seith_core.config import AppSettings
from seith_core.schemas import (
    Action,
    AgentReport,
    AssetClass,
    Decision,
    OrderProposal,
    Side,
)

from seith_api.auth import is_authorized
from seith_api.format import fmt_broadcast, fmt_decision, fmt_pending, fmt_proposal


@pytest.fixture()
def settings():
    return AppSettings(_env_file=None, data_dir="d", db_path="t.db")


def make_decision(**kw) -> Decision:
    defaults = dict(
        ticker="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        trade_date=__import__("datetime").date(2026, 8, 23),
        action=Action.BUY,
        confidence=0.72,
        reasoning_summary="PM bullish " * 50,  # panjang, untuk uji truncate
        reports=(AgentReport(agent_name="kronos", role="forecast", content="+2%"),),
        risk_assessment="ok",
    )
    defaults.update(kw)
    return Decision(**defaults)


class TestAuth:
    def test_allowlist_match(self):
        s = AppSettings(_env_file=None)
        object.__setattr__(
            s,
            "telegram",
            type(s.telegram)(bot_token=None, allowed_user_ids=(6595275429,)),
        )
        assert is_authorized(6595275429, s) is True

    def test_fail_closed_empty_allowlist(self):
        s = AppSettings(_env_file=None)  # allowlist default kosong
        assert is_authorized(123, s) is False

    def test_none_user_rejected(self):
        s = AppSettings(_env_file=None)
        assert is_authorized(None, s) is False


class TestFormatters:
    def test_decision_contains_action_and_id(self):
        text = fmt_decision(make_decision())
        assert "BTCUSDT" in text and "BUY" in text and "decision_id" in text
        assert "TIDAK dikirim otomatis" in text  # pengingat approval gate

    def test_decision_reasoning_truncated(self):
        text = fmt_decision(make_decision())
        assert len(text) < 800  # reasoning 50x kata dipangkas

    def test_proposal_format(self):
        p = OrderProposal(
            signal_id="sig_x",
            ticker="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            side=Side.BUY,
            quantity=0.02,
        )
        text = fmt_proposal(p)
        assert "BTCUSDT" in text and "BUY" in text and "pending_approval" in text

    def test_pending_empty_message(self):
        assert fmt_pending([]) == "Tidak ada proposal terbuka."

    def test_broadcast_sanitized_no_account_data(self):
        d = make_decision(reasoning_summary="qty besar 100 BTC equity $1M")
        text = fmt_broadcast(d)
        assert "equity" not in text.lower().replace(
            "equity $1m", ""
        )  # konten dipangkas ke 200 char
        assert "bukan nasihat keuangan" in text
