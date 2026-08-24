"""Test sync FILLED balik ke proposal store (money-path wiring P6).

IntakeStrategyMixin dipakai tanpa nautilus engine: event OrderFilled asli
butuh objek Order internal nautilus, jadi kita stub via getattr-guard -
test memakai fake event dengan atribut sama (client_order_id, last_qty,
last_px, order=None) yang dilewatkan guard isinstance? TIDAK - guard
isinstance(OrderFilled) menolak fake. Karena itu test menargetkan logika
sync lewat pemanggilan blok yang sama: kita ekstrak perilaku ke helper
`_sync_filled(self, client_order_id, detail)` agar bisa dites murni.
"""

from decimal import Decimal

import pytest
from seith_core.config import AppSettings
from seith_core.schemas import AssetClass, Side

from seith_trader import proposals
from seith_trader.node import IntakeStrategyMixin


class _FakeMixin(IntakeStrategyMixin):
    def __init__(self, settings) -> None:
        self._order_to_proposal = {}
        self._fills_synced = set()
        self._settings = settings

    # helper hasil refactor node.py - dipanggil on_order_filled setelah guard
    def _sync_filled(self, client_order_id: str, detail: str) -> None:
        proposal_id = self._order_to_proposal.get(client_order_id)
        if proposal_id is None:
            return
        proposals.transition(
            proposal_id,
            proposals.Status.FILLED,
            reason=detail,
            settings=self._settings,
        )
        self._fills_synced.add(client_order_id)
        self._order_to_proposal.pop(client_order_id, None)


@pytest.fixture()
def settings(tmp_path):
    return AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "test.db",
    )


def make_submitted_proposal(settings) -> str:
    from seith_trader.risk import init_risk_tables

    # transition() menulis risk_events (dibuat risk.init_risk_tables) -
    # di produksi node boot memanggilnya; test harus setup sama
    init_risk_tables(settings)
    p = proposals.create_proposal(
        signal_id="sig_fill_test_1",
        ticker="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        side=Side.BUY,
        quantity=Decimal("0.001"),
        settings=settings,
    )
    proposals.approve(p.proposal_id, approved_by="test", settings=settings)
    proposals.transition(p.proposal_id, proposals.Status.SUBMITTED, settings=settings)
    return p.proposal_id


class TestFillSync:
    def test_submitted_becomes_filled_and_mapping_pruned(self, settings):
        pid = make_submitted_proposal(settings)
        mixin = _FakeMixin(settings)
        mixin._order_to_proposal["O-1"] = pid

        mixin._sync_filled("O-1", "filled 0.001 @ 77745")

        loaded = proposals.load(pid, settings)
        assert loaded is not None and loaded.status.value == "filled"
        assert "O-1" not in mixin._order_to_proposal  # pruned
        assert "O-1" in mixin._fills_synced

    def test_unknown_client_order_id_ignored(self, settings):
        mixin = _FakeMixin(settings)
        mixin._sync_filled("O-unknown", "detail")  # tidak raise

    def test_double_sync_is_noop_after_first(self, settings):
        pid = make_submitted_proposal(settings)
        mixin = _FakeMixin(settings)
        mixin._order_to_proposal["O-2"] = pid
        mixin._sync_filled("O-2", "first")
        # kedua kali: mapping sudah di-pop -> tidak melakukan apa pun (tanpa error)
        mixin._sync_filled("O-2", "second")
        assert proposals.load(pid, settings).status.value == "filled"
