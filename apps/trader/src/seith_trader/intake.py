"""Signal intake: konsumsi proposal APPROVED -> risk check -> submitter.

Submitter adalah Protocol agar money-path logic bisa diuji penuh dengan
DryRunSubmitter tanpa menjalankan nautilus engine. Live node memakai
implementasi di seith_trader.executor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from seith_core.config import AppSettings, get_settings
from seith_core.schemas import OrderProposal
from seith_core.schemas import OrderProposalStatus as Status

from seith_trader import proposals, risk

logger = logging.getLogger("seith.trader")


class Submitter(Protocol):
    def submit(self, proposal: OrderProposal, quantity: Decimal) -> str | None:
        """Kirim order ke venue/engine; return order id venue atau None bila gagal."""
        ...


@dataclass(frozen=True)
class IntakeResult:
    proposal_id: str
    action: str  # submitted | risk_rejected | error
    detail: str = ""


def process_pending(
    mark_price_fn,
    portfolio_state,
    submitter: Submitter,
    settings: AppSettings | None = None,
) -> list[IntakeResult]:
    """Satu siklus intake: ambil semua APPROVED, evaluasi risk, eksekusi yang lolos."""
    s = settings or get_settings()
    results: list[IntakeResult] = []
    for proposal in proposals.list_by_status(Status.APPROVED, s):
        try:
            mark = mark_price_fn(proposal)
            decision = risk.evaluate(proposal, portfolio_state, mark_price=mark, settings=s)
            if not decision.approved:
                reason = "; ".join(decision.reasons)
                proposals.transition(
                    proposal.proposal_id, Status.CANCELLED, reason=f"risk: {reason}", settings=s
                )
                risk.record_risk_event("risk_reject", f"{proposal.proposal_id}: {reason}", s)
                logger.warning("[%s] ditolak RiskManager: %s", proposal.proposal_id, reason)
                results.append(IntakeResult(proposal.proposal_id, "risk_rejected", reason))
                continue

            assert decision.quantity is not None
            venue_order_id = submitter.submit(proposal, decision.quantity)
            if venue_order_id is None:
                proposals.transition(
                    proposal.proposal_id, Status.CANCELLED, reason="submit gagal", settings=s
                )
                results.append(IntakeResult(proposal.proposal_id, "error", "submit gagal"))
                continue
            proposals.transition(
                proposal.proposal_id,
                Status.SUBMITTED,
                approved_by=proposal.approved_by,
                settings=s,
            )
            risk.record_risk_event(
                "submitted",
                f"{proposal.proposal_id} qty={decision.quantity} venue_id={venue_order_id}",
                s,
            )
            logger.info(
                "[%s] SUBMITTED qty=%s venue=%s",
                proposal.proposal_id,
                decision.quantity,
                venue_order_id,
            )
            results.append(IntakeResult(proposal.proposal_id, "submitted"))
        except Exception as exc:  # noqa: BLE001 - satu proposal gagal tak boleh hentikan batch
            logger.exception("intake error %s", proposal.proposal_id)
            results.append(IntakeResult(proposal.proposal_id, "error", str(exc)))
    return results


def halt_all_pending(settings: AppSettings | None = None) -> int:
    """Kill switch: cancel semua PENDING/APPROVED yang belum dikirim."""
    s = settings or get_settings()
    cancelled = 0
    for status in (Status.PENDING_APPROVAL, Status.APPROVED):
        for p in proposals.list_by_status(status, s):
            if p.status is status:
                proposals.transition(p.proposal_id, Status.CANCELLED, reason="/halt", settings=s)
                cancelled += 1
    return cancelled
