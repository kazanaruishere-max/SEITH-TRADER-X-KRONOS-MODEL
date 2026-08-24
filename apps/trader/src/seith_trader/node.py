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

from seith_trader import risk
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
    """Mixin yang mengikat metode submit ke instance Strategy nautilus."""

    def submit_via_nautilus(
        self: Strategy, proposal: OrderProposal, quantity: Decimal
    ) -> str | None:
        instrument_id = f"{proposal.ticker}.BINANCE"
        try:
            args = build_market_order_args(proposal, quantity, instrument_id)
            order = self.order_factory.market(**args)
            self.submit_order(order)
            return str(order.client_order_id)
        except Exception as exc:  # noqa: BLE001 - dilaporkan ke intake sebagai gagal submit
            logger.exception("nautilus submit gagal untuk %s", proposal.proposal_id)
            # Detail error masuk DB - logging python bisa tertelan oleh logger internal nautilus.
            risk.record_risk_event("submit_error", f"{proposal.proposal_id}: {exc!r}")
            return None


def build_intake_strategy() -> tuple[Strategy, Submitter]:
    class _Intake(IntakeStrategyMixin, Strategy):
        def __init__(self) -> None:
            super().__init__(config=StrategyConfig(strategy_id="SEITH-INTAKE-001"))

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
    while True:
        try:
            equity = _equity_base()
            state = risk.PortfolioState(
                equity=equity,
                open_positions_count=0,
                daily_pnl=Decimal("0"),
                peak_equity=equity,
            )
            await asyncio.to_thread(process_pending, mark_price, state, submitter, s)
        except Exception:  # noqa: BLE001 - siklus harus selamat dari error tunggal
            logger.exception("siklus intake gagal (dilanjutkan)")
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
