"""Formatter pesan Telegram - pure functions agar mudah diuji."""

from __future__ import annotations

from seith_core.schemas import Decision, ForecastResult, OrderProposal
from seith_core.schemas import OrderProposalStatus as Status


def fmt_decision(d: Decision) -> str:
    lines = [
        f"📊 <b>{d.ticker}</b> — {d.action.value.upper()}",
        f"Confidence: {d.confidence:.0%} · {d.trade_date.isoformat()}",
        f"Alasan: {_truncate(d.reasoning_summary, 400)}",
        "",
        f"<i>decision_id:</i> <code>{d.decision_id}</code>",
        "Order TIDAK dikirim otomatis — pakai /pending lalu /approve &lt;id&gt;",
    ]
    return "\n".join(lines)


def fmt_proposal(p: OrderProposal) -> str:
    limit = f" @ {p.limit_price}" if p.limit_price else ""
    return (
        f"<code>{p.proposal_id}</code>\n"
        f"{p.ticker} {p.side.value.upper()} qty={p.quantity}{limit}\n"
        f"status: {p.status.value}"
    )


def fmt_pending(proposals_list: list[OrderProposal]) -> str:
    if not proposals_list:
        return "Tidak ada proposal terbuka."
    actionable = [
        p for p in proposals_list if p.status in (Status.PENDING_APPROVAL, Status.APPROVED)
    ]
    if not actionable:
        return "Tidak ada proposal terbuka."
    return "\n\n".join(fmt_proposal(p) for p in actionable)


def fmt_broadcast(decision: Decision) -> str:
    """Digest komunitas: TANPA ukuran posisi/PnL/detail akun (kebijakan sanitasi)."""
    return (
        f"🔔 <b>SEITH Analysis</b>\n"
        f"{decision.ticker}: sinyal <b>{decision.action.value.upper()}</b>"
        f" (conf {decision.confidence:.0%})\n"
        f"{_truncate(decision.reasoning_summary, 200)}\n"
        f"<i>{decision.trade_date.isoformat()} · bukan nasihat keuangan</i>"
    )


def _truncate(text: str, max_len: int) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= max_len else clean[: max_len - 3] + "..."


def fmt_forecast(fr: ForecastResult) -> str:
    sign = "+" if fr.expected_return >= 0 else ""
    return (
        f"🔮 <b>{fr.ticker}</b> — Kronos {fr.timeframe.value} x{fr.horizon_bars}\n"
        f"Expected return: {sign}{fr.expected_return:.2%}\n"
        f"Confidence: {fr.confidence:.0%}\n"
        f"<i>forecast_id:</i> <code>{fr.forecast_id}</code>\n"
        f"<i>{fr.asset_class.value} · path relatif {fr.ohlcv_path}</i>"
    )


def fmt_positions(positions: list[OrderProposal]) -> str:
    """Eksposur terbuka (order-intent) - BUKAN marked-to-market."""
    if not positions:
        return "Tidak ada posisi terbuka."
    lines = [
        f"· {p.ticker} {p.side.value.upper()} qty={p.quantity} · {p.status.value}"
        for p in positions
    ]
    return (
        "<b>Posisi terbuka</b> (eksposur order-intent, belum marked-to-market):\n"
        + "\n".join(lines)
    )
