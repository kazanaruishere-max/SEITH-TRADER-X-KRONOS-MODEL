"""Order proposal store + state machine.

Transisi legal:
  PENDING_APPROVAL -> APPROVED | REJECTED | CANCELLED
  APPROVED         -> SUBMITTED | CANCELLED
  SUBMITTED        -> FILLED | CANCELLED

Invariant tambahan di luar schema: trader node adalah pemilik kebenaran untuk
transisi SUBMITTED+ (lihat skill seith-trading-safety) - semua lewat fungsi
di modul ini, bukan update SQL bebas.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import TypeAdapter
from seith_core.config import AppSettings, get_settings
from seith_core.schemas import (
    AssetClass,
    OrderProposal,
    Side,
    Ticker,
)
from seith_core.schemas import (
    OrderProposalStatus as Status,
)

_validate_ticker = TypeAdapter(Ticker)

logger = logging.getLogger("seith.proposals")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_proposals (
    proposal_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    decision_id TEXT,
    ticker TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    order_type TEXT NOT NULL,
    limit_price TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    updated_at TEXT NOT NULL,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON order_proposals (status);
"""


def _connect(settings: AppSettings) -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    # bridge (proses lain) + intake (worker thread) menulis DB yang sama;
    # busy_timeout mencegah OperationalError sporadis saat lock burst
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_tables(settings: AppSettings | None = None) -> None:
    s = settings or get_settings()
    with closing(_connect(s)) as conn:
        conn.executescript(_SCHEMA)
        # anti double-create lintas siklus/restart: satu signal_id satu proposal
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_proposals_signal"
            " ON order_proposals(signal_id)"
        )
        conn.commit()


def _save(conn: sqlite3.Connection, p: OrderProposal) -> None:
    conn.execute(
        # INSERT murni (BUKAN OR REPLACE): UNIQUE(signal_id) wajib melempar
        # IntegrityError agar dedup bridge terdeteksi, bukan diam-diam menimpa.
        "INSERT INTO order_proposals"
        " (proposal_id, signal_id, decision_id, ticker, asset_class, side, quantity,"
        "  order_type, limit_price, status, created_at, approved_by, approved_at,"
        "  updated_at, raw_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            p.proposal_id,
            p.signal_id,
            p.decision_id,
            p.ticker,
            p.asset_class.value,
            p.side.value,
            str(p.quantity),
            p.order_type.value if hasattr(p.order_type, "value") else p.order_type,
            str(p.limit_price) if p.limit_price else None,
            p.status.value,
            p.created_at.isoformat(),
            p.approved_by,
            p.approved_at.isoformat() if p.approved_at else None,
            utcnow_iso(),
            p.model_dump_json(),
        ),
    )


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def create_proposal(
    *,
    signal_id: str,
    ticker: str,
    asset_class: AssetClass,
    side: Side,
    quantity: Decimal,
    decision_id: str | None = None,
    order_type: str = "market",
    limit_price: Decimal | None = None,
    settings: AppSettings | None = None,
) -> OrderProposal:
    """Idempoten per signal_id (UNIQUE index): duplikat return proposal existing."""
    s = settings or get_settings()
    init_tables(s)
    existing = load_by_signal_id(signal_id, s)
    if existing is not None:
        logger.info("proposal utk signal_id %s sudah ada (%s) - skip",
                    signal_id[:24], existing.proposal_id)
        return existing
    proposal = OrderProposal(
        signal_id=signal_id,
        ticker=_validate_ticker.validate_python(ticker),
        asset_class=asset_class,
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        decision_id=decision_id,
    )
    with closing(_connect(s)) as conn:
        try:
            _save(conn, proposal)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raced = load_by_signal_id(signal_id, s)
            if raced is not None:
                logger.info("race dedup: signal_id %s sudah ada - pakai existing",
                            signal_id[:24])
                return raced
            raise ValueError(f"integritas proposal gagal: {exc}") from exc
    return proposal


def load_by_signal_id(
    signal_id: str, settings: AppSettings | None = None
) -> OrderProposal | None:
    s = settings or get_settings()
    init_tables(s)
    with closing(_connect(s)) as conn:
        row = conn.execute(
            "SELECT raw_json FROM order_proposals WHERE signal_id = ?", (signal_id,)
        ).fetchone()
    return OrderProposal.model_validate_json(row["raw_json"]) if row else None


def load(proposal_id: str, settings: AppSettings | None = None) -> OrderProposal | None:
    s = settings or get_settings()
    init_tables(s)
    with closing(_connect(s)) as conn:
        row = conn.execute(
            "SELECT raw_json FROM order_proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
    return OrderProposal.model_validate_json(row["raw_json"]) if row else None


def list_by_status(status: Status, settings: AppSettings | None = None) -> list[OrderProposal]:
    s = settings or get_settings()
    init_tables(s)
    with closing(_connect(s)) as conn:
        rows = conn.execute(
            "SELECT raw_json FROM order_proposals WHERE status = ? ORDER BY created_at",
            (status.value,),
        ).fetchall()
    return [OrderProposal.model_validate_json(r["raw_json"]) for r in rows]


def transition(
    proposal_id: str,
    to_status: Status,
    *,
    approved_by: str | None = None,
    reason: str | None = None,
    settings: AppSettings | None = None,
) -> OrderProposal:
    """Satu-satunya pintu perubahan status. Transisi ilegal = error keras."""
    s = settings or get_settings()
    current = load(proposal_id, s)
    if current is None:
        raise KeyError(f"proposal '{proposal_id}' tidak ditemukan")

    allowed: dict[Status, set[Status]] = {
        Status.PENDING_APPROVAL: {Status.APPROVED, Status.REJECTED, Status.CANCELLED},
        Status.APPROVED: {Status.SUBMITTED, Status.CANCELLED},
        Status.SUBMITTED: {Status.FILLED, Status.CANCELLED},
        Status.FILLED: set(),
        Status.REJECTED: set(),
        Status.CANCELLED: set(),
    }
    if to_status not in allowed[current.status]:
        raise ValueError(f"transisi ilegal {current.status.value} -> {to_status.value}")
    if to_status in (Status.APPROVED, Status.SUBMITTED, Status.FILLED) and not (
        approved_by or current.approved_by
    ):
        raise ValueError(f"transisi ke {to_status.value} wajib approved_by")

    updated = current
    if approved_by and not current.approved_by:
        from seith_core.schemas import utcnow

        updated = OrderProposal.model_validate(
            {
                **current.model_dump(mode="json"),
                "approved_by": approved_by,
                "approved_at": utcnow().isoformat(),
            }
        )
    with closing(_connect(s)) as conn:
        _save_with_status(conn, updated, to_status, reason)
        conn.commit()
    result = load(proposal_id, s)
    assert result is not None
    return result


def _save_with_status(
    conn: sqlite3.Connection, base: OrderProposal, status: Status, reason: str | None
) -> None:
    data: dict[str, Any] = {**base.model_dump(mode="json"), "status": status.value}
    proposal = OrderProposal.model_validate(data)
    conn.execute(
        "UPDATE order_proposals SET status = ?, raw_json = ?, updated_at = ? WHERE proposal_id = ?",
        (status.value, proposal.model_dump_json(), utcnow_iso(), proposal.proposal_id),
    )
    if reason:
        conn.execute(
            "INSERT INTO risk_events (ts, kind, detail) VALUES (?, 'transition', ?)",
            (utcnow_iso(), f"{proposal.proposal_id} -> {status.value}: {reason}"),
        )


def approve(
    proposal_id: str, approved_by: str, settings: AppSettings | None = None
) -> OrderProposal:
    return transition(proposal_id, Status.APPROVED, approved_by=approved_by, settings=settings)


def reject(
    proposal_id: str, reason: str | None = None, settings: AppSettings | None = None
) -> OrderProposal:
    return transition(proposal_id, Status.REJECTED, reason=reason, settings=settings)
