"""Konteks hidup untuk /ask (Fase B2): portofolio, keputusan terakhir,
mode aktif, kalender hari ini. Robust per-sumber (satu gagal tak mematikan lain).

CATATAN: positions snapshot table dari node belum ada (P4). "Portofolio"
di sini DERIVASI dari proposal berstatus SUBMITTED/FILLED (eksposur order-intent),
BUKAN marked-to-market nyata. Dilabeli jujur di output.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seith_core.config import AppSettings, get_settings
from seith_core.schemas import OrderProposal
from seith_core.schemas import OrderProposalStatus as Status


def open_positions(settings: AppSettings | None = None) -> list[OrderProposal]:
    """Eksposur terbuka = proposal SUBMITTED/FILLED (belum ditutup)."""
    from seith_trader import proposals

    out: list[OrderProposal] = []
    for st in (Status.SUBMITTED, Status.FILLED):
        out.extend(proposals.list_by_status(st))
    return out


def build_ask_context(settings: AppSettings | None = None) -> str:
    """Rangkai konteks ringkas; setiap bagian di-wrap try/except sendiri."""
    s = settings or get_settings()
    parts: list[str] = []

    # 1. Mode aktif
    try:
        from seith_data.trading_mode import get_trading_mode

        mode, min_conf, _ = get_trading_mode(s)
        parts.append(f"[MODE] trading={mode.value} (auto-min-conf {min_conf:.0%})")
    except Exception as exc:  # noqa: BLE001
        parts.append(f"[MODE] tidak terbaca: {exc}")

    # 2. Portofolio (derivasi dari proposal)
    try:
        positions = open_positions(s)
        if positions:
            lines = [
                f"  - {p.ticker} {p.side.value.upper()} qty={p.quantity} ({p.status.value})"
                for p in positions[:10]
            ]
            parts.append("[PORTOFOLIO] posisi terbuka (order-intent):\n" + "\n".join(lines))
        else:
            parts.append("[PORTOFOLIO] tidak ada posisi terbuka.")
    except Exception as exc:  # noqa: BLE001
        parts.append(f"[PORTOFOLIO] tidak terbaca: {exc}")

    # 3. Keputusan analisis terakhir
    try:
        from seith_analysis.decision_store import load_recent

        rows = load_recent(limit=3)
        if rows:
            lines = [
                f"  - {r['ticker']} {str(r['action']).upper()} ({r['confidence']:.0%})"
                for r in rows
            ]
            parts.append("[ANALISIS] keputusan terakhir:\n" + "\n".join(lines))
    except Exception:  # noqa: BLE001
        pass

    # 4. Kalender hari ini
    try:
        from seith_data.events_store import load_economic_events

        now = datetime.now(UTC)
        events = load_economic_events(start=now, end=now + timedelta(days=1))
        if events:
            seen: set[tuple[str, str]] = set()
            lines = []
            for e in sorted(events, key=lambda x: x.scheduled_at):
                key = (e.scheduled_at.isoformat(), e.event_type)
                if key in seen:
                    continue
                seen.add(key)
                when = e.scheduled_at.strftime("%H:%M UTC")
                lines.append(f"  - {when} {e.currency} {e.event_type} ({e.importance.value})")
                if len(lines) >= 5:
                    break
            if lines:
                parts.append("[KALENDER] event hari ini:\n" + "\n".join(lines))
    except Exception:  # noqa: BLE001
        pass

    return "\n\n".join(parts)
