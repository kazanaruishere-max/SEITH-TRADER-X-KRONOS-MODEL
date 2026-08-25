"""Telegram bot control plane (personal) + channel broadcast.

Fail-closed: bot menolak start bila allowlist kosong (config.py::configured).
Semua handler di-guard auth; non-allowlist diabaikan diam-diam + log warn.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from seith_core.config import LLMSettings, get_settings
from seith_core.schemas import OrderProposalStatus as Status
from seith_data.trading_mode import TradingMode

from seith_api import chat_memory, intent, ui
from seith_api.auth import is_authorized, is_owner
from seith_api.format import fmt_broadcast, fmt_decision, fmt_forecast, fmt_positions
from seith_api.ratelimit import rate_limited

logger = logging.getLogger("seith.bot")
router = Router()


def _uid(message: Message) -> int | None:
    return message.from_user.id if message.from_user else None


def _cb_allowed(callback: CallbackQuery) -> bool:
    """Fail-closed: callback hanya dari user ter-allowlist."""
    uid = callback.from_user.id if callback.from_user else None
    return is_authorized(uid, get_settings()) and not rate_limited(uid)


def _guard(handler):
    """Read/command guard: allowlist + rate limit (viewer & owner)."""
    accepted = set(inspect.signature(handler).parameters) - {"message"}

    async def wrapper(message: Message, *args, **kwargs):
        settings = get_settings()
        uid = _uid(message)
        if rate_limited(uid):
            logger.warning("rate limit untuk %s", uid)
            return
        if not is_authorized(uid, settings):
            logger.warning("unauthenticated access dari %s - diabaikan", uid)
            return
        return await handler(message, **{k: v for k, v in kwargs.items() if k in accepted})

    return wrapper


def _guard_owner(handler):
    """Owner-only guard untuk command destruktif (analyze/approve/reject/halt/broadcast)."""
    accepted = set(inspect.signature(handler).parameters) - {"message"}

    async def wrapper(message: Message, *args, **kwargs):
        settings = get_settings()
        uid = _uid(message)
        if rate_limited(uid):
            logger.warning("rate limit untuk %s", uid)
            return
        if not is_authorized(uid, settings):
            logger.warning("unauthenticated access dari %s - diabaikan", uid)
            return
        if not is_owner(uid, settings):
            await message.answer("⛪ hanya OWNER yang boleh menjalankan perintah ini.")
            return
        return await handler(message, **{k: v for k, v in kwargs.items() if k in accepted})

    return wrapper


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
        "/mode [off|semi|auto] · /calendar · /ask <pertanyaan>\n"
        "<i>/pending, /mode, /recent, /calendar pakai tombol inline.</i>"
    )


@router.message(Command("mode"))
@_guard
async def cmd_mode(message: Message, command: CommandObject) -> None:
    """E4 TANGAN: saklar off/semi/auto. Default sistem SEMI.

    Tanpa argumen: kirim inline keyboard untuk switch cepat. Dengan argumen:
    tetap mendukung bentuk lama `/mode semi`.
    """
    from seith_data.trading_mode import get_trading_mode, set_trading_mode

    if command.args:
        if not is_owner(_uid(message)):
            await message.answer("⛪ hanya OWNER yang boleh mengganti mode.")
            return
        arg = command.args.strip().lower()
        try:
            new_mode = TradingMode(arg)
        except ValueError:
            await message.answer("❌ Mode tidak dikenal. Pilihan: off | semi | auto")
            return
        set_trading_mode(new_mode, updated_by=f"telegram:{_uid(message)}")
        await message.answer(f"✅ Mode trading → <b>{new_mode.value}</b>")
        return

    mode_now, min_conf, _ = get_trading_mode()
    await message.answer(
        f"mode saat ini: <b>{mode_now.value}</b> "
        f"(auto-min-confidence {min_conf:.0%})\n"
        "Pilih saklar di bawah:\n"
        "<i>off=abaikan sinyal news · semi=wajib approve manual · "
        "auto=proposal langsung APPROVED bila conf cukup (RiskManager tetap gerbang)</i>",
        reply_markup=ui.mode_keyboard(mode_now),
    )


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
    text, kb = ui.fmt_calendar_page(lines, page=0)
    await message.answer(text, reply_markup=kb)


@router.message(Command("ask"))
@_guard
async def cmd_ask(message: Message, command: CommandObject) -> None:
    """E5 BICARA: tanya market/kondisi akun dalam bahasa bebas.

    Intent router (Fase B2) memetakan pertanyaan ke aksi spesifik
    (forecast/positions/calendar) sebelum jatuh ke LLM generik ber-konteks
    + memori percakapan.
    """
    if not command.args:
        await message.answer(
            "Pemakaian: /ask <pertanyaan>\n"
            "Contoh: /ask prediksi BTCUSDT · /ask posisi saya · /ask event hari ini"
        )
        return

    question = command.args.strip()
    uid = _uid(message)
    intent_name, ticker = intent.classify_intent(question)

    async def run_and_reply() -> None:
        try:
            if intent_name == "FORECAST" and ticker:
                answer = await asyncio.to_thread(_run_forecast, ticker)
            elif intent_name == "POSITIONS" or intent_name == "PNL":
                answer = await asyncio.to_thread(_run_positions)
            elif intent_name == "CALENDAR":
                answer = _run_calendar_today()
            else:
                answer = await asyncio.to_thread(_ask_llm, question, uid)
            chat_memory.remember(uid, "user", question)
            chat_memory.remember(uid, "assistant", answer[:500])
            await message.answer(answer[:3500], parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001 - lapor gagal ke user
            logger.exception("ask gagal (intent=%s)", intent_name)
            await message.answer(f"❌ /ask gagal: {exc}")

    asyncio.create_task(run_and_reply())


@router.message(Command("forecast"))
@_guard
async def cmd_forecast(message: Message, command: CommandObject) -> None:
    """B2: panggil Kronos on-demand (GPU lokal ~3s) untuk satu ticker."""
    if not command.args:
        await message.answer("Pemakaian: /forecast BTCUSDT")
        return
    ticker = command.args.strip().upper().split()[0]
    await message.answer(f"🔮 Meminta forecast Kronos untuk {ticker}...")

    async def run_and_reply() -> None:
        try:
            answer = await asyncio.to_thread(_run_forecast, ticker)
            await message.answer(answer, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            logger.exception("forecast %s gagal", ticker)
            await message.answer(f"❌ Forecast {ticker} gagal: {exc}")

    asyncio.create_task(run_and_reply())


def _run_forecast(ticker: str) -> str:
    """Jalankan Kronos; param sesuai skill seith-kronos (lookback<=512, sc=8, T=1, top_p=0.9)."""
    from seith_analysis.kronos_service import forecast
    from seith_core.schemas import Timeframe

    fr = forecast(ticker, Timeframe.H1, horizon_bars=24, lookback=400, sample_count=8)
    return fmt_forecast(fr)


def _run_positions() -> str:
    from seith_api.context import open_positions

    return fmt_positions(open_positions())


def _run_calendar_today() -> str:
    from datetime import UTC, datetime, timedelta

    from seith_data.events_store import load_economic_events

    now = datetime.now(UTC)
    events = load_economic_events(start=now, end=now + timedelta(days=1))
    if not events:
        return "Tidak ada rilis ekonomi hari ini di store."
    seen: set[tuple[str, str]] = set()
    lines: list[str] = []
    for e in sorted(events, key=lambda x: x.scheduled_at):
        key = (e.scheduled_at.isoformat(), e.event_type)
        if key in seen:
            continue
        seen.add(key)
        when = e.scheduled_at.strftime("%H:%M UTC")
        lines.append(f"· {when} · {e.currency} {e.event_type} ({e.importance.value})")
    return "<b>Kalender hari ini:</b>\n" + "\n".join(lines)


def _resolve_chat_endpoint(llm: LLMSettings) -> str:
    """Endpoint akhir LLM: 9router (keyless) dulu, fallback OpenRouter langsung."""
    base = str(llm.router_base_url) if llm.router_base_url else None
    if base:
        return base.rstrip("/") + "/chat/completions"
    return "https://openrouter.ai/api/v1/chat/completions"


def _ask_llm(question: str, user_id: int) -> str:
    """Panggil LLM dengan konteks hidup + memori percakapan; tanpa secret di prompt."""
    import requests

    from seith_api.context import build_ask_context
    from seith_api.digest import build_daily_digest, collect_digest_inputs

    settings = get_settings()
    llm = settings.llm
    if llm.router_base_url is None and llm.api_key is None:
        raise RuntimeError(
            "LLM belum terkonfigurasi — set SEITH_LLM__ROUTER_BASE_URL (9router) "
            "atau SEITH_LLM__API_KEY"
        )
    live_context = build_ask_context(settings)
    history = chat_memory.recall_context(user_id)
    system = (
        "Kamu asisten trading pribadi SEITH (paper mode). Jawab singkat dalam "
        "bahasa pengguna berdasarkan konteks berikut. Jangan mengarang data di "
        "luar konteks.\n\n"
        f"[KONTEKS HIDUP]\n{live_context}\n\n"
        f"[RINGKASAN HARIAN]\n{build_daily_digest(**collect_digest_inputs(settings))}"
    )
    messages = [{"role": "system", "content": system}]
    if history:
        messages.append(
            {"role": "system", "content": f"[RIWAYAT PERCAKAPAN]\n{history}"}
        )
    messages.append({"role": "user", "content": question})
    headers: dict[str, str] = {"HTTP-Referer": "https://seith.ai", "X-Title": "SEITH"}
    if llm.api_key is not None:
        # Key dipakai sesuai target endpoint: auth internal 9router ATAU provider langsung.
        headers["Authorization"] = f"Bearer {llm.api_key.get_secret_value()}"
    resp = requests.post(
        _resolve_chat_endpoint(llm),
        headers=headers,
        json={
            "model": llm.quick_model,
            "messages": messages,
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
@_guard_owner
async def cmd_analyze(message: Message, command: CommandObject) -> None:
    if not command.args:
        await message.answer("Pemakaian: /analyze BTCUSDT")
        return
    ticker = command.args.strip().upper().split()[0]
    status = await message.answer(
        f"⏳ Analisis {ticker} dimulai (data→Kronos→debat agent)..."
    )

    progress = _ProgressNotifier(status, ticker)

    async def run_and_report() -> None:
        try:
            from seith_analysis.run_analysis import run_analysis

            decision = await asyncio.to_thread(
                run_analysis, ticker, on_progress=progress.emit
            )
            await progress.finish()
            await message.answer(fmt_decision(decision))
            settings = get_settings()
            if settings.telegram.channel_configured:
                from seith_api.broadcast import send_channel

                await send_channel(fmt_broadcast(decision))
        except Exception as exc:  # noqa: BLE001 - lapor gagal ke user, jangan senyap
            logger.exception("analyze %s gagal", ticker)
            await progress.fail(str(exc))
            await message.answer(f"❌ Analisis {ticker} gagal: {exc}")

    asyncio.create_task(run_and_report())
    asyncio.create_task(progress.poll())


@router.message(Command("pending"))
@_guard
async def cmd_pending(message: Message) -> None:
    from seith_trader import proposals

    items = proposals.list_by_status(Status.PENDING_APPROVAL)
    items += proposals.list_by_status(Status.APPROVED)
    text, kb = ui.fmt_pending_page(items, page=0)
    await message.answer(text, reply_markup=kb)


@router.message(Command("approve"))
@_guard_owner
async def cmd_approve(message: Message, command: CommandObject) -> None:
    await _decide(message, command, approve=True)


@router.message(Command("reject"))
@_guard_owner
async def cmd_reject(message: Message, command: CommandObject) -> None:
    await _decide(message, command, approve=False)


async def _decide(message: Message, command: CommandObject, approve: bool) -> None:
    if not command.args:
        await message.answer(f"Pemakaian: /{'approve' if approve else 'reject'} <proposal_id>")
        return
    proposal_id = command.args.strip().split()[0]
    result = _apply_decision(proposal_id, approve, f"telegram:{_uid(message)}")
    await message.answer(result)


def _apply_decision(proposal_id: str, approve: bool, by: str) -> str:
    """Core aksi approve/reject; dipakai command & inline keyboard.

    Return string hasil (siap dikirim ke user). Melempar KeyError/ValueError
    ditangani di sini agar pemanggil hanya meneruskan pesan.
    """
    from seith_trader import proposals

    try:
        if approve:
            p = proposals.approve(proposal_id, approved_by=by)
            return f"✅ {p.proposal_id} APPROVED — masuk antrean intake."
        p = proposals.reject(proposal_id, reason="ditolak via Telegram")
        return f"🚫 {p.proposal_id} REJECTED."
    except KeyError:
        return f"❌ Proposal '{proposal_id}' tidak ditemukan."
    except ValueError as exc:
        return f"⚠️ {exc}"


@router.message(Command("halt"))
@_guard_owner
async def cmd_halt(message: Message) -> None:
    from seith_trader.intake import halt_all_pending
    from seith_trader.risk import set_halt

    set_halt(True)
    cancelled = halt_all_pending()
    await message.answer(f"🛑 KILL SWITCH AKTIF. {cancelled} proposal terbuka dibatalkan.")


@router.message(Command("resume"))
@_guard_owner
async def cmd_resume(message: Message) -> None:
    from seith_trader.risk import set_halt

    set_halt(False)
    await message.answer("▶️ Kill switch dilepas. Intake kembali normal.")


@router.message(Command("positions"))
@_guard
async def cmd_positions(message: Message) -> None:
    from seith_api.context import open_positions

    await message.answer(fmt_positions(open_positions()), parse_mode="HTML")


@router.message(Command("pnl"))
@_guard
async def cmd_pnl(message: Message) -> None:
    from seith_api.context import open_positions

    positions = open_positions()
    if not positions:
        await message.answer("Tidak ada posisi terbuka (pnl = 0).")
        return
    # PnL nyata butuh mark price pasar; saat ini hanya eksposur order-intent.
    await message.answer(fmt_positions(positions), parse_mode="HTML")


@router.message(Command("risk"))
@_guard
async def cmd_risk(message: Message) -> None:
    from seith_trader.risk import is_halted

    from seith_api.context import open_positions

    settings = get_settings()
    limits = settings.risk
    halted = is_halted()
    open_count = len(open_positions())
    lines = [
        f"environment: <b>{settings.environment}</b>",
        f"kill switch: <b>{'AKTIF 🛑' if halted else 'off'}</b>",
        f"posisi terbuka: {open_count} / max {limits.max_open_positions}",
        f"max position: {limits.max_position_pct:.0%}",
        f"max daily loss: {limits.max_daily_loss_pct:.0%}",
        f"max drawdown: {limits.max_drawdown_pct:.0%}",
        f"require approval: {'ya' if limits.require_approval else 'tidak'}",
    ]
    await message.answer("<b>Risk (read-only):</b>\n" + "\n".join(lines), parse_mode="HTML")


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

    rows = load_recent(limit=50)
    text, kb = ui.fmt_recent_page(rows, page=0)
    await message.answer(text, reply_markup=kb)


@router.message(Command("broadcast"))
@_guard_owner
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


# ---------------------------------------------------------------------------
# Fase B1: handler inline keyboard (mode / approve-reject / pagination)
# ---------------------------------------------------------------------------


@router.callback_query(lambda c: c.data and c.data.startswith("mode:"))
async def cb_mode(callback: CallbackQuery) -> None:
    if not _cb_allowed(callback) or not is_owner(
        callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("⛪ hanya OWNER", show_alert=True)
        return
    from seith_data.trading_mode import get_trading_mode, set_trading_mode

    mode_val = callback.data.split(":", 1)[1]
    try:
        new_mode = TradingMode(mode_val)
    except ValueError:
        await callback.answer("mode tidak valid", show_alert=True)
        return
    set_trading_mode(new_mode, updated_by=f"telegram:{callback.from_user.id}")
    mode_now, _, _ = get_trading_mode()
    await callback.answer(f"mode → {new_mode.value}")
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"mode saat ini: <b>{mode_now.value}</b>\nPilih saklar di bawah:",
        reply_markup=ui.mode_keyboard(mode_now),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("dec:"))
async def cb_decide(callback: CallbackQuery) -> None:
    if not _cb_allowed(callback) or not is_owner(
        callback.from_user.id if callback.from_user else None
    ):
        await callback.answer("⛪ hanya OWNER", show_alert=True)
        return
    _, verb, proposal_id = callback.data.split(":", 2)
    result = _apply_decision(proposal_id, verb == "approve", f"telegram:{callback.from_user.id}")
    await callback.answer(result)

    # Segarkan daftar proposal pada pesan yang sama (kembali ke halaman 1).
    from seith_trader import proposals

    items = proposals.list_by_status(Status.PENDING_APPROVAL)
    items += proposals.list_by_status(Status.APPROVED)
    text, kb = ui.fmt_pending_page(items, page=0)
    try:
        await callback.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - pesan mungkin tak berubah
        logger.debug("refresh pending setelah aksi dilewati")


@router.callback_query(lambda c: c.data and c.data.startswith("pg:"))
async def cb_page(callback: CallbackQuery) -> None:
    if not _cb_allowed(callback):
        await callback.answer("⛔ tidak diizinkan", show_alert=True)
        return
    _, list_name, page_s = callback.data.split(":", 2)
    page = int(page_s)

    if list_name == "pending":
        from seith_trader import proposals

        items = proposals.list_by_status(Status.PENDING_APPROVAL)
        items += proposals.list_by_status(Status.APPROVED)
        text, kb = ui.fmt_pending_page(items, page)
    elif list_name == "recent":
        from seith_analysis.decision_store import load_recent

        rows = load_recent(limit=50)
        text, kb = ui.fmt_recent_page(rows, page)
    elif list_name == "calendar":
        from datetime import UTC, datetime, timedelta

        from seith_data.events_store import load_economic_events

        now = datetime.now(UTC)
        events = load_economic_events(start=now, end=now + timedelta(days=7))
        seen: set[tuple[str, str]] = set()
        lines: list[str] = []
        for e in sorted(events, key=lambda x: x.scheduled_at):
            key = (e.scheduled_at.isoformat(), e.event_type)
            if key in seen:
                continue
            seen.add(key)
            when = e.scheduled_at.strftime("%d %b %H:%M UTC")
            lines.append(f"· {when} · {e.currency} {e.event_type} ({e.importance.value})")
        text, kb = ui.fmt_calendar_page(lines, page)
    else:
        await callback.answer("list tidak dikenal")
        return

    await callback.answer()
    await callback.message.edit_text(text, reply_markup=kb)  # type: ignore[union-attr]


class _ProgressNotifier:
    """Streaming status /analyze: worker thread memanggil `emit`, task `poll`

    mengedit pesan secara berkala (hanya bila label berubah). Menghindari
    panggilan Telegram lintas-thread dengan polling lock-based.
    """

    def __init__(self, status_msg: Message, ticker: str) -> None:
        self._msg = status_msg
        self._ticker = ticker
        self._latest = f"⏳ {ticker}: memulai..."
        self._lock = threading.Lock()
        self._done = False

    def emit(self, label: str) -> None:
        with self._lock:
            self._latest = label

    async def poll(self) -> None:
        last = None
        while True:
            with self._lock:
                cur = self._latest
                done = self._done
            if cur != last:
                last = cur
                try:
                    await self._msg.edit_text(cur)
                except Exception:  # noqa: BLE001 - edit bisa gagal (msg lama)
                    logger.debug("progress edit dilewati")
            if done:
                return
            await asyncio.sleep(2)

    async def finish(self) -> None:
        with self._lock:
            self._done = True

    async def fail(self, err: str) -> None:
        with self._lock:
            self._latest = f"❌ {self._ticker}: {err}"
            self._done = True


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


_POLL_INTERVAL_S = 45


async def _proposal_poller(bot: Bot) -> None:
    """B3: push proposal PENDING_APPROVAL baru ke owner dengan tombol aksi.

    Idempoten: proposal yang sudah di-push (atau sudah tak PENDING) tak dikirim
    ulang. Kegagalan satu iterasi tak mematikan loop.
    """
    seen: set[str] = set()
    while True:
        try:
            settings = get_settings()
            if not settings.telegram.allowed_user_ids:
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue
            owner = settings.telegram.allowed_user_ids[0]
            from seith_trader import proposals

            pending = proposals.list_by_status(Status.PENDING_APPROVAL)
            for p in pending:
                if p.proposal_id in seen:
                    continue
                seen.add(p.proposal_id)
                text, kb = ui.fmt_proposal_action(p)
                await bot.send_message(owner, "🔔 Proposal baru:\n" + text, reply_markup=kb)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - poller gagal tak boleh matikan bot
            logger.exception("iterasi proposal poller gagal - retry")
        await asyncio.sleep(_POLL_INTERVAL_S)


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
    poller_task = asyncio.create_task(_proposal_poller(bot))

    def _digest_died(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("digest loop MATI permanen", exc_info=task.exception())

    def _poller_died(task: asyncio.Task) -> None:  # noqa: ARG001 - parity w/ digest
        if not task.cancelled() and task.exception() is not None:
            logger.error("proposal poller MATI permanen", exc_info=task.exception())

    digest_task.add_done_callback(_digest_died)
    poller_task.add_done_callback(_poller_died)
    logger.info("SEITH bot polling... (allowlist: %s)", list(settings.telegram.allowed_user_ids))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
