"""Telegram bot control plane (personal) + channel broadcast.

Fail-closed: bot menolak start bila allowlist kosong (config.py::configured).
Semua handler di-guard auth; non-allowlist diabaikan diam-diam + log warn.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from seith_core.config import get_settings
from seith_core.schemas import OrderProposalStatus as Status

from seith_api.auth import is_authorized
from seith_api.format import fmt_broadcast, fmt_decision, fmt_pending

logger = logging.getLogger("seith.bot")
router = Router()


def _uid(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


def _guard(handler):
    async def wrapper(message: Message, *args, **kwargs):
        settings = get_settings()
        if not is_authorized(_uid(message), settings):
            logger.warning("unauthenticated access dari %s - diabaikan", _uid(message))
            return
        return await handler(message, *args, **kwargs)

    return wrapper


@router.message(CommandStart())
@_guard
async def cmd_start(message: Message) -> None:
    await message.answer(
        "SEITH control plane aktif.\n"
        "Perintah: /analyze <ticker> · /pending · /approve <id> · /reject <id>\n"
        "/halt · /resume · /status · /recent · /broadcast <teks>"
    )


@router.message(Command("analyze"))
@_guard
async def cmd_analyze(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Pemakaian: /analyze BTCUSDT")
        return
    ticker = command.args.strip().upper().split()[0]
    await message.answer(f"⏳ Analisis {ticker} dimulai (data→Kronos→debat agent)...")

    async def run_and_report() -> None:
        try:
            from seith_analysis.run_analysis import run_analysis

            decision = await asyncio.to_thread(run_analysis, ticker)
            await message.answer(fmt_decision(decision))
            settings = get_settings()
            if settings.telegram.channel_configured:
                from seith_api.broadcast import send_channel

                await send_channel(fmt_broadcast(decision))
        except Exception as exc:  # noqa: BLE001 - lapor gagal ke user, jangan senyap
            logger.exception("analyze %s gagal", ticker)
            await message.answer(f"❌ Analisis {ticker} gagal: {exc}")

    asyncio.create_task(run_and_report())


@router.message(Command("pending"))
@_guard
async def cmd_pending(message: Message) -> None:
    from seith_trader import proposals

    items = proposals.list_by_status(Status.PENDING_APPROVAL)
    items += proposals.list_by_status(Status.APPROVED)
    await message.answer(fmt_pending(items))


@router.message(Command("approve"))
@_guard
async def cmd_approve(message: Message, command: CommandObject) -> None:
    await _decide(message, command, approve=True)


@router.message(Command("reject"))
@_guard
async def cmd_reject(message: Message, command: CommandObject) -> None:
    await _decide(message, command, approve=False)


async def _decide(message: Message, command: CommandObject, approve: bool) -> None:
    if not command.args:
        await message.answer(f"Pemakaian: /{'approve' if approve else 'reject'} <proposal_id>")
        return
    proposal_id = command.args.strip().split()[0]
    from seith_trader import proposals

    try:
        if approve:
            p = proposals.approve(proposal_id, approved_by=f"telegram:{_uid(message)}")
            await message.answer(f"✅ {p.proposal_id} APPROVED — masuk antrean intake.")
        else:
            p = proposals.reject(proposal_id, reason="ditolak via Telegram")
            await message.answer(f"🚫 {p.proposal_id} REJECTED.")
    except KeyError:
        await message.answer(f"❌ Proposal '{proposal_id}' tidak ditemukan.")
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")


@router.message(Command("halt"))
@_guard
async def cmd_halt(message: Message) -> None:
    from seith_trader.intake import halt_all_pending
    from seith_trader.risk import set_halt

    set_halt(True)
    cancelled = halt_all_pending()
    await message.answer(f"🛑 KILL SWITCH AKTIF. {cancelled} proposal terbuka dibatalkan.")


@router.message(Command("resume"))
@_guard
async def cmd_resume(message: Message) -> None:
    from seith_trader.risk import set_halt

    set_halt(False)
    await message.answer("▶️ Kill switch dilepas. Intake kembali normal.")


@router.message(Command("status"))
@_guard
async def cmd_status(message: Message) -> None:
    from seith_trader.risk import is_halted

    settings = get_settings()
    halted = is_halted()
    await message.answer(
        f"environment: <b>{settings.environment}</b>\n"
        f"kill switch: <b>{'AKTIF 🛑' if halted else 'off'}</b>\n"
        f"LLM provider: {settings.llm.provider} ({settings.llm.quick_model})\n"
        f"channel broadcast: {'on' if settings.telegram.channel_configured else 'off'}\n\n"
        "<i>/positions & /pnl aktif setelah trader node live (P4 boot)</i>"
    )


@router.message(Command("recent"))
@_guard
async def cmd_recent(message: Message) -> None:
    from seith_analysis.decision_store import load_recent

    rows = load_recent(limit=5)
    if not rows:
        await message.answer("Belum ada keputusan tersimpan.")
        return
    lines = [
        f"{str(r['created_at'])[:16]} · {r['ticker']} · "
        f"{str(r['action']).upper()} ({r['confidence']:.0%})"
        for r in rows
    ]
    await message.answer("<b>Keputusan terakhir:</b>\n" + "\n".join(lines))


@router.message(Command("broadcast"))
@_guard
async def cmd_broadcast(message: Message, command: CommandObject) -> None:
    settings = get_settings()
    if not settings.telegram.channel_configured:
        await message.answer("Channel belum dikonfigurasi (SEITH_TELEGRAM__CHANNEL_ID).")
        return
    if not command.args:
        await message.answer("Pemakaian: /broadcast <teks>")
        return
    from seith_api.broadcast import send_channel

    ok = await send_channel(command.args)
    await message.answer("📡 Terkirim ke channel." if ok else "❌ Gagal kirim ke channel.")


async def send_channel(text: str) -> bool:
    settings = get_settings()
    if not settings.telegram.channel_configured:
        return False
    try:
        bot = Bot(token=settings.telegram.bot_token.get_secret_value())  # type: ignore[union-attr]
        async with bot.session:
            await bot.send_message(settings.telegram.channel_id, text)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("broadcast channel gagal")
        return False


async def main() -> None:
    settings = get_settings()
    if not settings.telegram.configured:
        raise RuntimeError(
            "Telegram belum terkonfigurasi dengan benar (token + allowlist wajib) - fail-closed."
        )
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    bot = Bot(token=settings.telegram.bot_token.get_secret_value())  # type: ignore[union-attr]
    dp = Dispatcher()
    dp.include_router(router)
    logger.info("SEITH bot polling... (allowlist: %s)", list(settings.telegram.allowed_user_ids))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
