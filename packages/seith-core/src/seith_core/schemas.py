"""Domain schemas shared across all SEITH services.

Semua model immutable (frozen) dan tervalidasi. Serialisasi round-trip via
`model_dump_json()` / `model_validate_json()` adalah kontrak antar-service.

Kebijakan kontrak wire (lihat docs/adr/0002-contract-and-ops-policies.md):
- Domain models di sini STRICT: extra field ditolak, timestamp wajib aware-UTC,
  ticker dinormalisasi. Evolusi versi ditangani `SCHEMA_VERSION` pada envelope
  transport, bukan dengan melonggarkan validasi domain.
- Status order adalah invariant, bukan sumber kebenaran: service konsumen
  (trader node) wajib verifikasi ulang approval terhadap catatannya sendiri.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "1"


def utcnow() -> datetime:
    """Aware UTC timestamp - satu-satunya cara membuat timestamp di SEITH."""
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """ID dengan entropy penuh (uuid4 hex 32 karakter) untuk audit trail."""
    return f"{prefix}_{uuid.uuid4().hex}"


class _Model(BaseModel):
    """Base: frozen (immutable), forbids extra fields agar typo tertangkap."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class AssetClass(StrEnum):
    CRYPTO = "crypto"
    EQUITY_US = "equity_us"
    FOREX = "forex"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Action(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class Timeframe(StrEnum):
    """Interval bar kanonik gaya ccxt; satu-satunya kosakata timeframe SEITH.

    Konversi dari/yfinance/nautilus terjadi di boundary masing-masing data
    source, bukan di kontrak ini.
    """

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class SignalSource(StrEnum):
    TRADING_AGENTS = "trading_agents"
    KRONOS = "kronos"
    MANUAL = "manual"


class OrderProposalStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUBMITTED = "submitted"
    FILLED = "filled"
    CANCELLED = "cancelled"


#: Status yang secara semantik menandakan order sudah melewati gerbang approval.
_APPROVED_STATUSES = frozenset(
    {
        OrderProposalStatus.APPROVED,
        OrderProposalStatus.SUBMITTED,
        OrderProposalStatus.FILLED,
    }
)

Ticker = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_upper=True,
        min_length=1,
        max_length=20,
        # (?i): pattern dievaluasi SEBELUM to_upper oleh pydantic-core,
        # jadi charset check harus case-insensitive terhadap input mentah.
        pattern=r"(?i)^[A-Z0-9][A-Z0-9._-]*$",
    ),
]
"""Simbol aset ternormalisasi (uppercase, tanpa spasi) yang aman dipakai
sebagai nama file Parquet dan lookup simbol exchange."""


def action_to_side(action: Action) -> Side:
    """Konversi eksplisit intent -> order side; HOLD tidak punya side."""
    if action is Action.BUY:
        return Side.BUY
    if action is Action.SELL:
        return Side.SELL
    raise ValueError("HOLD tidak memiliki side order")


def _validate_relative_data_path(path: str) -> str:
    if ".." in path or path.startswith(("/", "\\")) or ":" in path:
        raise ValueError("ohlcv_path wajib path relatif di bawah data dir")
    return path


class RiskLimits(_Model):
    max_position_pct: float = Field(default=0.10, gt=0.0, le=1.0)
    max_daily_loss_pct: float = Field(default=0.03, gt=0.0, le=1.0)
    max_drawdown_pct: float = Field(default=0.10, gt=0.0, le=1.0)
    max_open_positions: int = Field(default=10, ge=1)
    require_approval: bool = True


class ForecastResult(_Model):
    """Output Kronos inference service."""

    forecast_id: str = Field(default_factory=lambda: new_id("fc"))
    ticker: Ticker
    asset_class: AssetClass
    timeframe: Timeframe
    horizon_bars: int = Field(gt=0)
    generated_at: AwareDatetime = Field(default_factory=utcnow)
    expected_return: float
    confidence: float = Field(ge=0.0, le=1.0)
    ohlcv_path: str

    @field_validator("ohlcv_path")
    @classmethod
    def _relative_path_only(cls, v: str) -> str:
        return _validate_relative_data_path(v)


class Signal(_Model):
    """Signal trading terstandar yang dikirim analysis service ke trader node."""

    signal_id: str = Field(default_factory=lambda: new_id("sig"))
    ticker: Ticker
    asset_class: AssetClass
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    source: SignalSource
    rationale: str
    created_at: AwareDatetime = Field(default_factory=utcnow)
    strategy_hint: str | None = None
    forecast_id: str | None = None


class AgentReport(_Model):
    """Satu laporan dari satu agen dalam pipeline TradingAgents."""

    agent_name: str
    role: str
    content: str
    verdict: str | None = None


class Decision(_Model):
    """Keputusan akhir pipeline analisis multi-agent + audit trail lengkap."""

    decision_id: str = Field(default_factory=lambda: new_id("dec"))
    ticker: Ticker
    asset_class: AssetClass
    trade_date: date
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str
    reports: tuple[AgentReport, ...] = ()
    risk_assessment: str
    forecast_id: str | None = None
    created_at: AwareDatetime = Field(default_factory=utcnow)


class OrderProposal(_Model):
    """Proposal order hasil signal.

    Invariant approval: status APPROVED/SUBMITTED/FILLED wajib membawa
    identitas approver. Transisi antar-status diverifikasi ulang oleh service
    (decision store), bukan oleh model tunggal - model hanya menolak objek
    yang secara internal tidak konsisten.
    """

    proposal_id: str = Field(default_factory=lambda: new_id("ord"))
    signal_id: str
    decision_id: str | None = None
    ticker: Ticker
    asset_class: AssetClass
    side: Side
    quantity: Decimal = Field(gt=Decimal("0"))
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    status: OrderProposalStatus = OrderProposalStatus.PENDING_APPROVAL
    created_at: AwareDatetime = Field(default_factory=utcnow)
    approved_by: str | None = None
    approved_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _validate_order_invariants(self) -> OrderProposal:
        if self.order_type is OrderType.LIMIT:
            if self.limit_price is None or self.limit_price <= 0:
                raise ValueError("limit orders require a positive limit_price")
        elif self.limit_price is not None:
            raise ValueError("market orders must not carry limit_price")
        if self.status in _APPROVED_STATUSES and not self.approved_by:
            raise ValueError(
                f"status '{self.status}' requires non-empty approved_by "
                "(approval gate wajib, lihat PRD FR-E3)"
            )
        return self


class PositionSnapshot(_Model):
    """Snapshot satu posisi untuk dashboard & Telegram reporting."""

    ticker: Ticker
    asset_class: AssetClass
    side: Side
    quantity: Decimal
    avg_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    updated_at: AwareDatetime = Field(default_factory=utcnow)

    @model_validator(mode="before")
    @classmethod
    def _accept_own_computed_field(cls, data: object) -> object:
        """Round-trip: output serialisasi memuat computed_field; terima kembali
        tanpa menolaknya sebagai unknown field (extra=forbid)."""
        if isinstance(data, dict):
            data.pop("unrealized_pnl_pct", None)
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unrealized_pnl_pct(self) -> float:
        cost = self.avg_price * self.quantity
        if cost == 0:
            return 0.0
        return float(self.unrealized_pnl / cost * 100)
