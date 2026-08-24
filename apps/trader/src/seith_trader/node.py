"""SEITH trader live node: Binance market data + Sandbox execution (paper) + intake.

Semua API vendor di file ini sudah diverifikasi empiris di v1.231:
TradingNodeConfig/BinanceDataClientConfig/SandboxExecutionClientConfig/
SandboxLiveExecClientFactory/Strategy.order_factory/Strategy.submit_order.
Boot verification penuh = item testing batch.

Loop intake memakai asyncio task eksternal yang memanggil metode Strategy
terverifikasi - bukan semantik timer vendor yang belum kuterifikasi.
"""

from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal

import ccxt
from nautilus_trader.adapters.binance import (
    BINANCE,
    BinanceAccountType,
    BinanceDataClientConfig,
    BinanceLiveDataClientFactory,
)
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.config import (
    CacheConfig,
    InstrumentProviderConfig,
    LiveExecEngineConfig,
    LoggingConfig,
    MessageBusConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy
from seith_core.config import AppSettings, get_settings
from seith_core.schemas import OrderProposal

from seith_trader import proposals, risk
from seith_trader.executor import build_market_order_args
from seith_trader.intake import Submitter, process_pending

logger = logging.getLogger("seith.node")

_INSTRUMENTS = ("BTCUSDT", "ETHUSDT")
_INSTRUMENT_IDS = frozenset(
    InstrumentId.from_str(f"{t}.BINANCE") for t in _INSTRUMENTS
)
_INTAKE_INTERVAL_SECONDS = 10


def _equity_base() -> Decimal:
    """Equity awal paper account - placeholder boot; diganti event fill di P6."""
    return Decimal(os.environ.get("SEITH_TRADER__EQUITY", "10000"))


def ticker_to_ccxt(ticker: str) -> str:
    from seith_data.sources.binance import to_ccxt_symbol

    return to_ccxt_symbol(ticker)


class IntakeStrategyMixin:
    """Mixin yang mengikat metode submit ke instance Strategy nautilus.

    Mapping order->proposal PER-INSTANCE (diinisialisasi subclass di __init__),
    bukan class attribute - shared mutable antar instance = cross-pollution.
    """

    _order_to_proposal: dict[str, str]
    _fills_synced: set[str]

    def submit_via_nautilus(
        self: Strategy, proposal: OrderProposal, quantity: Decimal
    ) -> str | None:
        instrument_id = f"{proposal.ticker}.BINANCE"
        try:
            args = build_market_order_args(proposal, quantity, instrument_id)
            order = self.order_factory.market(**args)
            # mapping SEBELUM submit (M-2): fill event bisa diproses event-loop
            # sebelum baris setelah submit_order sempat dieksekusi
            self._order_to_proposal[str(order.client_order_id)] = proposal.proposal_id
            self.submit_order(order)
            return str(order.client_order_id)
        except Exception:  # noqa: BLE001 - dilaporkan ke intake sebagai gagal submit
            logger.exception("nautilus submit gagal untuk %s", proposal.proposal_id)
            return None

    def on_order_filled(self, event) -> None:
        """Sync status FILLED balik ke proposal store (wiring P6 yang tadinya bolong).

        Trader node adalah pemilik kebenaran transisi SUBMITTED+ (skill
        seith-trading-safety): event fill dari engine, bukan field wire.
        Guard: partial-fill (order belum status FILLED penuh) TIDAK ditransisi;
        sync per client_order_id sekali saja agar tidak jadi exception noise.
        """
        from nautilus_trader.model.enums import OrderStatus
        from nautilus_trader.model.events import OrderFilled

        if not isinstance(event, OrderFilled):
            return
        client_order_id = str(event.client_order_id)
        if client_order_id in self._fills_synced:
            return
        order = getattr(event, "order", None)
        if order is not None and order.status is not OrderStatus.FILLED:
            logger.debug("partial fill %s - tunggu fill final", client_order_id[:24])
            return
        self._sync_filled(client_order_id, f"filled {event.last_qty} @ {event.last_px}")

    def _sync_filled(self, client_order_id: str, detail: str) -> None:
        """Blok sync murni (tanpa tipe nautilus) agar dapat diuji langsung."""
        proposal_id = self._order_to_proposal.get(client_order_id)
        if proposal_id is None:
            return
        try:
            proposals.transition(
                proposal_id,
                proposals.Status.FILLED,
                reason=detail,
            )
            self._fills_synced.add(client_order_id)
            self._order_to_proposal.pop(client_order_id, None)  # cegah growth tak terbatas
            logger.info("[%s] FILLED ter-sync ke proposal store", proposal_id)
        except Exception:  # noqa: BLE001 - kegagalan sync tak boleh ganggu node
            logger.exception("sync FILLED gagal utk %s", proposal_id)


def build_intake_strategy() -> tuple[Strategy, Submitter]:
    class _Intake(IntakeStrategyMixin, Strategy):
        def __init__(self) -> None:
            super().__init__(config=StrategyConfig(strategy_id="SEITH-INTAKE-001"))
            self._order_to_proposal = {}
            self._fills_synced = set()

        def on_start(self) -> None:
            # Subscribe market data agar sandbox punya harga utk simulasi fill.
            for t in _INSTRUMENTS:
                self.subscribe_quote_ticks(InstrumentId.from_str(f"{t}.BINANCE"))

    strategy = _Intake()

    class NautilusSubmitter:
        def submit(self, proposal: OrderProposal, quantity: Decimal) -> str | None:
            return strategy.submit_via_nautilus(proposal, quantity)

    return strategy, NautilusSubmitter()


async def intake_loop(node_ref: dict, settings: AppSettings | None = None) -> None:
    """Siklus periodik: mark price ccxt public -> risk check -> submit via strategy."""
    s = settings or get_settings()
    exchange = ccxt.binance({"enableRateLimit": True})
    strategy, submitter = build_intake_strategy()

    while node_ref.get("node") is None:  # tunggu assembly selesai
        await asyncio.sleep(0.5)
    node_ref["node"].trader.add_strategy(strategy)

    def mark_price(proposal: OrderProposal) -> Decimal:
        t = exchange.fetch_ticker(ticker_to_ccxt(proposal.ticker))
        return Decimal(str(t["last"]))

    logger.info("intake loop aktif tiap %ds: %s", _INTAKE_INTERVAL_SECONDS, list(_INSTRUMENTS))
    # H-1 (partial): state portofolio PERSISTEN lintas siklus - peak_equity
    # monotonik & open-positions dihitung dari status proposal nyata. CATATAN
    # JUJUR: daily_pnl masih 0 karena equity akun sandbox belum ter-wire
    # (butuh API nautilus portfolio yang belum terverifikasi) - breaker
    # daily-loss tetap INERT sampai wiring itu; drawdown baru hidup saat
    # equity mulai bergerak; max-open-positions sudah LIVE.
    peak_equity = _equity_base()
    fail_counts: dict[str, int] = {}  # H-2 layer 2: zombie intake guard

    def _refresh_state() -> risk.PortfolioState:
        nonlocal peak_equity
        base = _equity_base()
        filled_open = proposals.list_by_status(proposals.Status.SUBMITTED, s) + [
            p for p in proposals.list_by_status(proposals.Status.FILLED, s)
        ]
        state = risk.PortfolioState(
            equity=base,
            open_positions_count=len(filled_open),
            daily_pnl=Decimal("0"),  # TODO(wiring akun): dari fill PnL nyata
            peak_equity=max(peak_equity, base),
        )
        peak_equity = state.peak_equity
        return state

    while True:
        try:
            state = _refresh_state()
            results = await asyncio.to_thread(
                process_pending, mark_price, state, submitter, s
            )
        except Exception:  # noqa: BLE001 - siklus harus selamat dari error tunggal
            logger.exception("siklus intake gagal (dilanjutkan)")
            results = []
        # H-2 layer 2: proposal yang berulang gagal intake -> CANCELLED final
        for r in results:
            if r.action == "error":
                fail_counts[r.proposal_id] = fail_counts.get(r.proposal_id, 0) + 1
            else:
                fail_counts.pop(r.proposal_id, None)
        zombies = [pid for pid, n in fail_counts.items() if n >= 10]
        for pid in zombies:
            logger.error("proposal %s gagal intake x10 - CANCELLED", pid)
            proposals.transition(pid, proposals.Status.CANCELLED,
                                 reason="intake gagal permanen x10")
            fail_counts.pop(pid, None)
        await asyncio.sleep(_INTAKE_INTERVAL_SECONDS)


def build_node() -> TradingNode:
    config = TradingNodeConfig(
        trader_id="SEITH-001",
        logging=LoggingConfig(log_level="INFO"),
        exec_engine=LiveExecEngineConfig(reconciliation=False),
        cache=CacheConfig(),
        message_bus=MessageBusConfig(),
        data_clients={
            BINANCE: BinanceDataClientConfig(
                account_type=BinanceAccountType.SPOT,
                instrument_provider=InstrumentProviderConfig(load_ids=_INSTRUMENT_IDS),
            ),
        },
        exec_clients={
            "SANDBOX": SandboxExecutionClientConfig(
                venue=BINANCE,
                starting_balances=[f"{_equity_base():n} USDT"],
            ),
        },
    )
    node = TradingNode(config=config)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory("SANDBOX", SandboxLiveExecClientFactory)
    return node


async def amain() -> None:
    node_ref: dict = {}
    loop_task = asyncio.create_task(intake_loop(node_ref))
    node = build_node()
    node_ref["node"] = node
    node.build()
    try:
        await node.run_async()
    finally:
        loop_task.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Handler eksplisit utk logger seith.* - selamat dari takeover logging oleh nautilus.
    _app_fh = logging.FileHandler("seith_app.log", encoding="utf-8")
    _app_fh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger("seith").addHandler(_app_fh)
    asyncio.run(amain())
