"""News Bridge (E3 wiring): trigger engine -> proposal lewat jalur money-path.

Siklus periodik:
  events store (window pasca-rilis) -> scan_releases (trigger policy)
  -> GATE mode trading (off=skip, semi=pending manusia, auto=APPROVED system:auto
     bila confidence>=threshold) -> proposals.create_proposal
  -> notifikasi Telegram personal (+broadcast channel tersanitasi utk semi/auto).

INVARIANT TIER-0 yang dijaga modul ini:
- Tidak ada submit order langsung ke venue dari sini - satu-satunya jalur
  eksekusi tetap intake node (RiskManager wajib mengevaluasi semua proposal,
  termasuk yang APPROVED oleh system:auto).
- OFF dan /halt menghentikan pembuatan proposal; kill switch intake tetap ada.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape
from pathlib import Path

import requests
from seith_core.config import get_settings
from seith_core.news_trigger import scan_releases
from seith_core.schemas import Action, PatternSummary, Side, Signal
from seith_data.events_store import load_economic_events
from seith_data.trading_mode import TradingMode, get_trading_mode

from seith_trader import proposals
from seith_trader.node import _INSTRUMENTS

logger = logging.getLogger("seith.news_bridge")

SCAN_INTERVAL_SECONDS = 60
LOOKBACK_MINUTES = 35  # window trigger 30m + margin fetch
PLACEHOLDER_QUANTITY = Decimal("0.001")  # nominal; sizing final = RiskManager

#: Allowlist ticker yang boleh jadi proposal news - HARUS subset instrument
#: yang di-load node (H-2: proposal cross-asset akan zombie di intake karena
#: mark_price ccxt tidak mengenalnya).
ALLOWED_TICKERS = frozenset(_INSTRUMENTS)

#: Proposal lebih muda dari ini dianggap BARU (baru layak notifikasi).
_FRESH_SECONDS = 20

TELEGRAM_API = "https://api.telegram.org"


def load_pattern_library(path: Path) -> list[PatternSummary]:
    """Muat library JSON hasil build E2; file rusak = degrade ke kosong (log)."""
    if not path.exists():
        logger.warning("pattern library belum ada: %s", path)
        return []
    import json

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("pattern library tidak terbaca (%s) - pakai kosong", type(exc).__name__)
        return []
    patterns: list[PatternSummary] = []
    for item in raw.get("patterns", []):
        try:
            patterns.append(PatternSummary.model_validate(item))
        except Exception as exc:  # noqa: BLE001 - data korup tak boleh matikan bridge
            logger.debug("pattern tidak valid dilewati (%s)", exc)
    return patterns


def _send_telegram(
    token: str | None, chat_id: int | str, text: str
) -> bool:
    """Kirim pesan via Bot API; gagal jangan pernah mematikan bridge."""
    if not token or chat_id in (None, ""):
        return False
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        return resp.ok
    except requests.RequestException as exc:
        # log tipe exception saja - body bisa memuat URL/token
        logger.warning("telegram send gagal (%s)", type(exc).__name__)
        return False


def _sanitize_for_channel(signal: Signal) -> str:
    """Broadcast channel: TANPA detail akun/id internal - agregat edukatif saja."""
    direction = "LONG" if signal.action is Action.BUY else "SHORT"
    return (
        f"<b>SEITH Signal</b>\n"
        f"{direction} {signal.ticker} (news-driven)\n"
        f"Arah historis pola mendukung kelanjutan impuls. "
        f"Confidence {float(signal.confidence):.0%}. "
        f"Bukan nasihat keuangan."
    )


def _notify_signal(tg, sig: Signal, mode_for_this, proposal_id: str,
                   notify_fn) -> None:
    """Push personal + broadcast channel tersanitasi; gagal kirim tak fatal."""
    personal = (
        f"<b>SINYAL NEWS</b> {sig.action.value.upper()} {escape(sig.ticker)}\n"
        f"conf {float(sig.confidence):.0%} · mode {mode_for_this.value}\n"
        f"<i>{escape(sig.rationale)}</i>\n"
        f"proposal {proposal_id}"
    )
    token = tg.bot_token.get_secret_value() if tg.bot_token else None
    if notify_fn is not None:
        notify_fn(personal)
    elif tg.allowed_user_ids and token:
        _send_telegram(token, tg.allowed_user_ids[0], personal)
    if tg.channel_configured and token:
        _send_telegram(token, tg.channel_id, _sanitize_for_channel(sig))


def process_signals_once(
    now: datetime,
    settings=None,
    notify_fn=None,
) -> list[str]:
    """Satu siklus scan+gate+create-proposal. Return daftar proposal_id baru."""
    s = settings or get_settings()
    # max_notional sengaja tidak dipakai di sini: sizing final adalah wewenang
    # RiskManager saat intake (proposal.quantity hanya nominal placeholder).
    mode, min_conf, _max_notional = get_trading_mode(s)
    if mode is TradingMode.OFF:
        logger.info("mode=off - skip scan")
        return []

    end = now.astimezone(UTC)
    start = end - timedelta(minutes=LOOKBACK_MINUTES)
    events = load_economic_events(start=start, end=end, min_importance=None, settings=s)

    lib_path: Path = s.data_dir / "patterns" / "pattern_library.json"
    patterns = load_pattern_library(lib_path)
    signals = scan_releases(events, patterns, now=end)

    created: list[str] = []
    for sig in signals:
        # H-2 defense layer 1: ticker di luar allowlist node DITOLAK di sumber
        if sig.ticker.upper() not in ALLOWED_TICKERS:
            logger.warning("sinyal %s di luar allowlist node - dibuang", sig.ticker)
            continue
        # AUTO hanya utk confidence >= threshold; sisanya turun ke SEMI-flow.
        mode_for_this = (
            TradingMode.AUTO
            if mode is TradingMode.AUTO and float(sig.confidence) >= min_conf
            else TradingMode.SEMI
        )

        proposal = proposals.create_proposal(
            signal_id=sig.signal_id,
            ticker=sig.ticker,
            asset_class=sig.asset_class,
            side=_side_from_action(sig),
            quantity=PLACEHOLDER_QUANTITY,
            decision_id=None,
            settings=s,
        )
        if mode_for_this is TradingMode.AUTO and proposal.status.value == "pending_approval":
            proposals.approve(proposal.proposal_id, "system:auto", settings=s)
        created.append(proposal.proposal_id)

        # M-4: notifikasi hanya untuk proposal BENAR-BENAR baru (idempotent
        # dedup mengembalikan existing pada siklus berikutnya - jangan spam).
        # Freshness diukur dari WALL CLOCK nyata, bukan `now` argumen (yang
        # bisa jadi jam simulasi saat testing/replay).
        age = (datetime.now(UTC) - proposal.created_at).total_seconds()
        if age <= _FRESH_SECONDS:
            _notify_signal(s.telegram, sig, mode_for_this, proposal.proposal_id,
                           notify_fn)

        logger.info("proposal dibuat %s (%s/%s conf=%.2f)",
                    proposal.proposal_id, mode_for_this.value,
                    sig.action.value, sig.confidence)
    return created


def _side_from_action(sig: Signal) -> Side:
    return Side.BUY if sig.action is Action.BUY else Side.SELL


async def run_forever() -> None:
    """Entrypoint `python -m seith_trader.news_bridge`."""
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    logger.info("news bridge aktif tiap %ds", SCAN_INTERVAL_SECONDS)
    while True:
        started = time.monotonic()
        try:
            process_signals_once(datetime.now(UTC))
        except Exception:  # noqa: BLE001 - siklus harus selamat
            logger.exception("siklus news bridge gagal (dilanjutkan)")
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(5.0, SCAN_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    asyncio.run(run_forever())
