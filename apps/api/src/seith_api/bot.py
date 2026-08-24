"""Telegram bot control plane (personal) + channel broadcast.

Fail-closed: bot menolak start bila allowlist kosong (config.py::configured).
Semua handler di-guard auth; non-allowlist diabaikan diam-diam + log warn.
"""

from __future__ import annotations

import asyncio
import inspect
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
    accepted = set(inspect.signature(handler).parameters) - {"message"}

    async def wrapper(message: Message, *args, **kwargs):
        settings = get_settings()
        if not is_authorized(_uid(message), settings):
            logger.warning("unauthenticated access dari %s - diabaikan", _uid(message))
            return
        return await handler(message, **{k: v for k, v in kwargs.items() if k in accepted})

    return wrapper


@router.message(CommandStart())
@_guard
async def cmd_start(message: Message) -> None:
    await message.answer(
        "SEITH control plane aktif.\n"
        "Perintah: /analyze <ticker> · /pending · /approve <id> · /reject <id>\n"
        "/halt · /resume · /status · /recent · /broadcast <teks>\n"
        "/mode [off|semi|auto] · /calendar · /ask <pertanyaan>"
    )


@router.message(Command("mode"))
@_guard
async def cmd_mode(message: Message, command: CommandObject) -> None:
    """E4 TANGAN: saklar off/semi/auto. Default sistem SEMI."""
    from seith_data.trading_mode import TradingMode, get_trading_mode, set_trading_mode

    mode_now, min_conf, _ = get_trading_mode()
    if not command.args:
        await message.answer(
            f"mode saat ini: <b>{mode_now.value}</b> "
            f"(auto-min-confidence {min_conf:.0%})\n"
            "Ubah: /mode off | semi | auto\n"
            "<i>off=abaikan sinyal news · semi=wajib approve manual · "
            "auto=proposal langsung APPROVED bila conf cukup (RiskManager tetap gerbang)</i>"
        )
        return
    arg = command.args.strip().lower()
    try:
        new_mode = TradingMode(arg)
    except ValueError:
        await message.answer("❌ Mode tidak dikenal. Pilihan: off | semi | auto")
        return
    set_trading_mode(new_mode, updated_by=f"telegram:{_uid(message)}")
    await message.answer(f"✅ Mode trading → <b>{new_mode.value}</b>")


@router.message(Command("calendar"))
@_guard
async def cmd_calendar(message: Message) -> None:
    """E6: rilis ekonomi 7 hari ke depan dari events store."""
    from datetime import UTC, datetime, timedelta

    from seith_data.events_store import load_economic_events

    now = datetime.now(UTC)
    events = load_economic_events(start=now, end=now + timedelta(days=7))
    if not events:
        await message.answer("Tidak ada rilis terjadwal 7 hari ke depan di store.")
        return
    seen: set[tuple[str, str]] = set()
    lines: list[str] = []
    for e in sorted(events, key=lambda x: x.scheduled_at):
        key = (e.scheduled_at.isoformat(), e.event_type)
        if key in seen:  # satu rilis dipetakan ke banyak ticker - tampilkan sekali
            continue
        seen.add(key)
        when = e.scheduled_at.strftime("%d %b %H:%M UTC")
        lines.append(f"· {when} · {e.currency} {e.event_type} ({e.importance.value})")
        if len(lines) >= 12:
            break
    await message.answer("<b>Kalender 7 hari:</b>\n" + "\n".join(lines))


@router.message(Command("ask"))
@_guard
async def cmd_ask(message: Message, command: CommandObject) -> None:
    """E5 BICARA MVP: tanya market/kondisi akun dalam bahasa bebas via LLM."""
    if not command.args:
        await message.answer("Pemakaian: /ask bagaimana kondisi BTC hari ini?")
        return

    async def run_and_reply() -> None:
        try:
            answer = await asyncio.to_thread(_ask_llm, command.args.strip())
            await message.answer(answer[:3500])
        except Exception as exc:  # noqa: BLE001 - lapor gagal ke user
            logger.exception("ask gagal")
            await message.answer(f"❌ /ask gagal: {exc}")

    asyncio.create_task(run_and_reply())


def _ask_llm(question: str) -> str:
    """Panggil LLM dengan konteks ringkas kontrol-plane; tanpa secret di prompt."""
    import requests

    from seith_api.digest import build_daily_digest, collect_digest_inputs

    settings = get_settings()
    if settings.llm.api_key is None:
        raise RuntimeError("LLM belum terkonfigurasi (SEITH_LLM__API_KEY)")
    context = build_daily_digest(**collect_digest_inputs(settings))
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm.api_key.get_secret_value()}"},
        json={
            "model": settings.llm.quick_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Kamu asisten trading pribadi SEITH (paper mode). Jawab "
                        "singkat dalam bahasa pengguna berdasarkan konteks berikut. "
                        "Jangan mengarang data di luar konteks.\n\n" + context
                    ),
                },
                {"role": "user", "content": question},
            ],
            "max_tokens": 700,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    choice = data.get("choices", [{}])[0].get("message", {}).get("content")
    if not choice:
        raise RuntimeError(f"jawaban LLM kosong: {str(data)[:120]}")
    return str(choice)


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


# CATATAN M-3 (security review): implementasi send_channel kini SATU sumber di
# seith_api/broadcast.py - duplikat lokal dihapus karena membuat modul itu
# tampak opsional padahal /analyze & /broadcast meng-import-nya.


async def _digest_loop(bot: Bot) -> None:
    """E6: kirim digest harian jam 13:00 WIB (Asia/Jakarta) ke owner (+channel).

    Hardened: kegagalan satu iterasi TIDAK mematikan loop (no silent death).
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from seith_api.digest import build_daily_digest, collect_digest_inputs

    while True:
        try:
            jakarta = ZoneInfo("Asia/Jakarta")
            now = datetime.now(jakarta)
            target = now.replace(hour=13, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            settings = get_settings()
            text = build_daily_digest(**collect_digest_inputs(settings))
            if settings.telegram.allowed_user_ids:
                await bot.send_message(settings.telegram.allowed_user_ids[0], text)
            if settings.telegram.channel_configured:
                await bot.send_message(settings.telegram.channel_id, text)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - digest gagal tak boleh matikan bot
            logger.exception("iterasi digest gagal - retry siklus berikutnya")
        await asyncio.sleep(60)  # hindari double-fire dalam menit yang sama


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
    digest_task = asyncio.create_task(_digest_loop(bot))

    def _digest_died(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("digest loop MATI permanen", exc_info=task.exception())

    digest_task.add_done_callback(_digest_died)
    logger.info("SEITH bot polling... (allowlist: %s)", list(settings.telegram.allowed_user_ids))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
