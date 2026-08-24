"""Digest harian (E6 MULUT): ringkasan kontrol-plane + event mendatang.

Murni pembentukan teks - IO disuntikkan dari caller agar mudah dites.
Sanitasi: tidak pernah memuat detail akun/secret; hanya agregat operasional.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seith_core.schemas import OrderProposalStatus as Status


def build_daily_digest(
    *,
    environment: str,
    halted: bool,
    mode: str,
    pending_count: int,
    approved_count: int,
    submitted_today: int,
    upcoming_events: list[tuple[str, str, str]],  # (waktu_iso16, ticker, event_type)
    generated_at: datetime | None = None,
) -> str:
    """Susun teks digest; dipanggil scheduler bot tiap hari."""
    ts = (generated_at or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"<b>SEITH Digest Harian</b> ({ts})",
        f"env {environment} · kill-switch {'🛑 AKTIF' if halted else 'off'} · mode <b>{mode}</b>",
        f"proposal pending={pending_count} approved={approved_count} "
        f"submitted/filled (total)={submitted_today}",
    ]
    if upcoming_events:
        lines.append("<b>Rilis 24 jam ke depan:</b>")
        for when, ticker, etype in upcoming_events[:5]:
            lines.append(f"  · {when} · {ticker} · {etype}")
    else:
        lines.append("Tidak ada rilis terjadwal 24 jam ke depan.")
    return "\n".join(lines)


def collect_digest_inputs(
    settings,
    now: datetime | None = None,
) -> dict:
    """Kumpulkan data nyata utk digest dari DB lokal (proposals/events/mode)."""
    from seith_data.events_store import load_economic_events
    from seith_data.trading_mode import get_trading_mode
    from seith_trader import proposals
    from seith_trader.risk import is_halted

    now = now or datetime.now(UTC)
    mode, _, _ = get_trading_mode(settings)
    pending = len(proposals.list_by_status(Status.PENDING_APPROVAL))
    approved = len(proposals.list_by_status(Status.APPROVED))

    day_end = now + timedelta(hours=24)
    events = load_economic_events(
        start=now, end=day_end, min_importance=None, settings=settings
    )
    upcoming = sorted(
        {
            (
                e.scheduled_at.astimezone(UTC).strftime("%m-%d %H:%M"),
                e.ticker,
                e.event_type,
            )
            for e in events
        }
    )
    return {
        "environment": settings.environment,
        "halted": is_halted(settings),
        "mode": mode.value,
        "pending_count": pending,
        "approved_count": approved,
        # submitted-today butuh query per tanggal; MVP pakai total submitted
        "submitted_today": _submitted_total(settings),
        "upcoming_events": upcoming,
    }


def _submitted_total(settings) -> int:
    from seith_trader import proposals

    return len(proposals.list_by_status(Status.SUBMITTED)) + len(
        proposals.list_by_status(Status.FILLED)
    )
