"""RiskManager murni - TIDAK mengenal nautilus, sepenuhnya unit-testable.

Invariant (PRD FR-E3, skill seith-trading-safety):
- 100% order WAJIB melewati evaluate() di sini sebelum sampai eksekusi.
- Kill switch aktif => semua submission ditolak.
- Daily loss / drawdown tembus limit => circuit breaker membuka, tolak baru
  sampai di-reset manual oleh operator (bukan otomatis close sendiri).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal

from seith_core.config import AppSettings, get_settings
from seith_core.schemas import (
    AssetClass,
    OrderProposal,
    RiskLimits,
    utcnow,
)


@dataclass(frozen=True)
class PortfolioState:
    """Snapshot minimal yang dibutuhkan risk check (dari cache/portfolio)."""

    equity: Decimal
    open_positions_count: int
    daily_pnl: Decimal  # negatif = loss hari ini
    peak_equity: Decimal  # high-water mark untuk drawdown


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...] = field(default=())
    quantity: Decimal | None = None


def _halt_active(settings: AppSettings) -> bool:
    """Kill switch global dibaca dari tabel ops_flags (ditulis /halt)."""
    with closing_conn(settings) as conn:
        row = conn.execute("SELECT value FROM ops_flags WHERE key = 'halt'").fetchone()
    return bool(row and row["value"] == "1")


def closing_conn(settings: AppSettings):
    import contextlib

    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return contextlib.closing(conn)


def init_risk_tables(settings: AppSettings | None = None) -> None:
    s = settings or get_settings()
    with closing_conn(s) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ops_flags (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                detail TEXT NOT NULL
            );
            """
        )
        conn.commit()


def set_halt(active: bool, settings: AppSettings | None = None) -> None:
    """Kill switch global. Hanya operator (Telegram /halt) yang memanggil ini."""
    s = settings or get_settings()
    init_risk_tables(s)
    with closing_conn(s) as conn:
        conn.execute(
            "INSERT INTO ops_flags (key, value, updated_at) VALUES ('halt', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            ("1" if active else "0", utcnow().isoformat()),
        )
        conn.commit()


def is_halted(settings: AppSettings | None = None) -> bool:
    s = settings or get_settings()
    init_risk_tables(s)
    return _halt_active(s)


def compute_order_quantity(
    equity: Decimal,
    price: Decimal,
    asset_class: AssetClass,
    limits: RiskLimits,
) -> Decimal:
    """Sizing fixed-fractional: qty = equity * max_position_pct / price.

    Crypto: 8 desimal; saham/forex: 2 desimal. Qty nol bila di bawah lot minimum.
    """
    if price <= 0 or equity <= 0:
        return Decimal(0)
    notional = equity * Decimal(str(limits.max_position_pct))
    raw_qty = notional / price
    precision = 8 if asset_class is AssetClass.CRYPTO else 2
    quantum = Decimal(10) ** -precision
    return raw_qty.quantize(quantum, rounding="ROUND_DOWN")


def evaluate(
    proposal: OrderProposal,
    portfolio: PortfolioState,
    mark_price: Decimal,
    limits: RiskLimits | None = None,
    settings: AppSettings | None = None,
) -> RiskDecision:
    """Gerbang pra-eksekusi. Return alasan eksplisit untuk setiap penolakan.

    `mark_price` = harga referensi terkini untuk sizing market order.
    """
    limits = limits or get_settings().risk
    reasons: list[str] = []

    if is_halted(settings):
        reasons.append("kill switch aktif (/halt)")

    # Circuit breaker: drawdown dari peak equity
    if portfolio.peak_equity > 0:
        dd = (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity
        if dd >= Decimal(str(limits.max_drawdown_pct)):
            reasons.append(f"drawdown {dd:.2%} >= limit {limits.max_drawdown_pct:.0%}")

    # Daily loss breaker
    if portfolio.daily_pnl < 0 and portfolio.equity > 0:
        loss_pct = -portfolio.daily_pnl / portfolio.equity
        if loss_pct >= Decimal(str(limits.max_daily_loss_pct)):
            reasons.append(f"daily loss {loss_pct:.2%} >= limit {limits.max_daily_loss_pct:.0%}")

    # Batas jumlah posisi terbuka (BUY menambah eksposur)
    if proposal.side.value == "buy" and portfolio.open_positions_count >= limits.max_open_positions:
        reasons.append(f"posisi terbuka {portfolio.open_positions_count} >= limit")

    if proposal.quantity <= 0:
        reasons.append("quantity proposal tidak valid")
        return RiskDecision(approved=False, reasons=tuple(reasons))

    if mark_price <= 0:
        reasons.append("mark price tidak valid")
        return RiskDecision(approved=False, reasons=tuple(reasons))

    if reasons:
        return RiskDecision(approved=False, reasons=tuple(reasons))

    sized = compute_order_quantity(portfolio.equity, mark_price, proposal.asset_class, limits)
    effective_qty = min(proposal.quantity, sized)
    if effective_qty <= 0:
        reasons.append("sizing menghasilkan qty nol (equity/harga)")
        return RiskDecision(approved=False, reasons=tuple(reasons))
    return RiskDecision(approved=True, quantity=effective_qty)


def record_risk_event(kind: str, detail: str, settings: AppSettings | None = None) -> None:
    s = settings or get_settings()
    init_risk_tables(s)
    with closing_conn(s) as conn:
        conn.execute(
            "INSERT INTO risk_events (ts, kind, detail) VALUES (?, ?, ?)",
            (utcnow().isoformat(), kind, detail),
        )
        conn.commit()
