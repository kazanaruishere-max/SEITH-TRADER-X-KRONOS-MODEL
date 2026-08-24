"""Broadcast channel Telegram satu arah (E6) - konten tersanitasi saja.

Modul ini sebelumnya HILANG: bot.py meng-import-nya secara lazy sehingga
`import seith_api` lolos tapi /broadcast crash saat dipakai (M-3 security
review). Kini sumber tunggal; duplikat di bot.py sudah dihapus.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from seith_core.config import get_settings

logger = logging.getLogger("seith.broadcast")


async def send_channel(text: str) -> bool:
    """Kirim teks ke channel bila terkonfigurasi; return sukses."""
    settings = get_settings()
    if not settings.telegram.channel_configured:
        logger.warning("broadcast dilewati: channel belum dikonfigurasi")
        return False
    try:
        bot = Bot(token=settings.telegram.bot_token.get_secret_value())  # type: ignore[union-attr]
        async with bot.session:
            await bot.send_message(settings.telegram.channel_id, text)
        return True
    except Exception:  # noqa: BLE001 - gagal broadcast tak boleh ganggu caller
        logger.exception("broadcast channel gagal")
        return False
