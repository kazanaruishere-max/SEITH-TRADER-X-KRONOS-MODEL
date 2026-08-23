"""Submitter implementations: DryRun (test) + nautilus glue (runtime).

Nautilus types di-import LAZY di dalam fungsi agar unit test money-path
tidak butuh engine ter-load.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from seith_core.schemas import OrderProposal

logger = logging.getLogger("seith.trader")


@dataclass
class DryRunSubmitter:
    """Merekam semua submission untuk assertion test. Tidak pernah menyentuh jaringan."""

    submissions: list[tuple[str, str, str]] = field(default_factory=list)  # (pid, side, qty)
    fail_next: bool = False

    def submit(self, proposal: OrderProposal, quantity: Decimal) -> str | None:
        if self.fail_next:
            self.fail_next = False
            return None
        self.submissions.append((proposal.proposal_id, proposal.side.value, str(quantity)))
        return f"dry-{proposal.proposal_id}"


def build_market_order_args(proposal: OrderProposal, quantity: Decimal, instrument_id: str) -> dict:
    """Argumen untuk strategy.order_factory.market(...) - dipanggil dari live node."""
    from nautilus_trader.model.enums import OrderSide
    from nautilus_trader.model.objects import Quantity

    side = OrderSide.BUY if proposal.side.value == "buy" else OrderSide.SELL
    precision = 8 if proposal.asset_class.value == "crypto" else 2
    return {
        "instrument_id": instrument_id,
        "order_side": side,
        "quantity": Quantity.from_str(f"{quantity:.{precision}f}"),
        "time_in_force": None,
    }
