"""Inline keyboard + pagination helpers untuk bot Telegram (Fase B1).

Murni (tanpa I/O DB/jaringan): menerima data yang sudah di-fetch, mengembalikan
tuple (teks, InlineKeyboardMarkup). Mudah diuji tanpa Telegram nyata.

Format callback_data (max 64 byte, aiogram):
  mode:<off|semi|auto>                  -> ubah mode trading
  dec:<approve|reject>:<proposal_id>   -> aksi pada satu proposal
  pg:<pending|recent|calendar>:<page>  -> ganti halaman list
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from seith_core.schemas import OrderProposal
from seith_core.schemas import OrderProposalStatus as Status
from seith_data.trading_mode import TradingMode

PAGE_SIZE = 5


def _page_bounds(total: int, page: int) -> tuple[int, int, bool, bool]:
    """Return (start, end_exclusive, has_prev, has_next) untuk `page` 0-based."""
    if total == 0:
        return 0, 0, False, False
    page = max(0, min(page, (total - 1) // PAGE_SIZE))
    start = page * PAGE_SIZE
    return start, min(start + PAGE_SIZE, total), page > 0, start + PAGE_SIZE < total


def _pager_row(list_name: str, page: int, has_prev: bool, has_next: bool):
    """Satu baris tombol prev/next; None bila tak ada pilihan."""
    buttons: list[InlineKeyboardButton] = []
    if has_prev:
        buttons.append(
            InlineKeyboardButton(text="◀ prev", callback_data=f"pg:{list_name}:{page - 1}")
        )
    if has_next:
        buttons.append(
            InlineKeyboardButton(text="next ▶", callback_data=f"pg:{list_name}:{page + 1}")
        )
    return buttons or None


def fmt_pending_page(items: list[OrderProposal], page: int) -> tuple[str, InlineKeyboardMarkup]:
    """Render satu halaman proposal + tombol aksi per item.

    Hanya PENDING_APPROVAL mendapat pasangan approve/reject; item APPROVED
    hanya bisa dibatalkan (reject) untuk mencegah double-approve.
    """
    actionable = [p for p in items if p.status in (Status.PENDING_APPROVAL, Status.APPROVED)]
    start, end, has_prev, has_next = _page_bounds(len(actionable), page)
    page_items = actionable[start:end]

    if not page_items:
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        return "Tidak ada proposal terbuka.", kb

    blocks: list[str] = []
    kb_rows: list[list[InlineKeyboardButton]] = []
    for p in page_items:
        limit = f" @ {p.limit_price}" if p.limit_price else ""
        blocks.append(
            f"<code>{p.proposal_id}</code>\n"
            f"{p.ticker} {p.side.value.upper()} qty={p.quantity}{limit} · "
            f"status: {p.status.value}"
        )
        if p.status is Status.PENDING_APPROVAL:
            kb_rows.append(
                [
                    InlineKeyboardButton(
                        text="✅ approve", callback_data=f"dec:approve:{p.proposal_id}"
                    ),
                    InlineKeyboardButton(
                        text="🚫 reject", callback_data=f"dec:reject:{p.proposal_id}"
                    ),
                ]
            )
        else:  # APPROVED -> hanya bisa batal
            kb_rows.append(
                [
                    InlineKeyboardButton(
                        text="✕ batal", callback_data=f"dec:reject:{p.proposal_id}"
                    )
                ]
            )

    pager = _pager_row("pending", page, has_prev, has_next)
    if pager:
        kb_rows.append(pager)
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    header = f"📋 Proposal terbuka (halaman {page + 1}):\n"
    return header + "\n\n".join(blocks), kb


def fmt_recent_page(rows: list[dict], page: int) -> tuple[str, InlineKeyboardMarkup]:
    start, end, has_prev, has_next = _page_bounds(len(rows), page)
    page_rows = rows[start:end]
    if not page_rows:
        return "Belum ada keputusan tersimpan.", InlineKeyboardMarkup(inline_keyboard=[])
    lines = [
        f"{str(r['created_at'])[:16]} · {r['ticker']} · "
        f"{str(r['action']).upper()} ({r['confidence']:.0%})"
        for r in page_rows
    ]
    pager = _pager_row("recent", page, has_prev, has_next)
    kb_rows: list[list[InlineKeyboardButton]] = [[pager]] if pager else []
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    return "<b>Keputusan terakhir:</b>\n" + "\n".join(lines), kb


def fmt_calendar_page(lines: list[str], page: int) -> tuple[str, InlineKeyboardMarkup]:
    start, end, has_prev, has_next = _page_bounds(len(lines), page)
    page_lines = lines[start:end]
    if not page_lines:
        return "Tidak ada rilis terjadwal.", InlineKeyboardMarkup(inline_keyboard=[])
    pager = _pager_row("calendar", page, has_prev, has_next)
    kb_rows: list[list[InlineKeyboardButton]] = [[pager]] if pager else []
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    return "<b>Kalender ekonomi:</b>\n" + "\n".join(page_lines), kb


def mode_keyboard(current: TradingMode) -> InlineKeyboardMarkup:
    labels = {
        TradingMode.OFF: "off (abaikan sinyal)",
        TradingMode.SEMI: "semi (wajib approve)",
        TradingMode.AUTO: "auto (langsung APPROVED)",
    }
    row: list[InlineKeyboardButton] = []
    for mode in (TradingMode.OFF, TradingMode.SEMI, TradingMode.AUTO):
        mark = "✓ " if mode is current else ""
        row.append(
            InlineKeyboardButton(
                text=f"{mark}{labels[mode]}", callback_data=f"mode:{mode.value}"
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[row])


def fmt_proposal_action(p: OrderProposal) -> tuple[str, InlineKeyboardMarkup]:
    """Satu proposal + tombol approve/reject untuk push proaktif (B3)."""
    from seith_api.format import fmt_proposal

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ approve", callback_data=f"dec:approve:{p.proposal_id}"
                ),
                InlineKeyboardButton(
                    text="🚫 reject", callback_data=f"dec:reject:{p.proposal_id}"
                ),
            ]
        ]
    )
    return fmt_proposal(p), kb
