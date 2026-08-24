"""Berita crypto via CryptoPanic free API (E1 MATA).

Endpoint: GET /api/free/v1/posts/?auth_token=...&currencies=BTC,ETH&public=true
Token gratis dari cryptopanic.com/developers. Field votes bisa absen pada
beberapa post - dinormalisasi ke 0, bukan error.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import requests
from pydantic import ValidationError
from seith_core.config import get_settings
from seith_core.schemas import NewsItem

from seith_data.sources.economic_calendar import get_json_retry

logger = logging.getLogger(__name__)

CRYPTOPANIC_URL = "https://cryptopanic.com/api/free/v1/posts/"


def _normalize_votes(raw: object) -> tuple[int, int]:
    """votes CryptoPanic: {'positive': n, 'negative': m} atau dict parsial."""
    if not isinstance(raw, dict):
        return 0, 0
    positive = raw.get("positive", 0)
    negative = raw.get("negative", 0)
    try:
        return max(0, int(positive)), max(0, int(negative))
    except (TypeError, ValueError):
        return 0, 0


def _parse_published(raw: object) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.debug("published_at tidak valid dilewati: %r", raw)
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def fetch_cryptopanic_posts(
    auth_token: str | None = None,
    currencies: str = "BTC,ETH",
    session: requests.Session | None = None,
) -> list[NewsItem]:
    """Ambil halaman terakhir berita crypto; return item tervalidasi schema."""
    token = auth_token
    if not token:
        secret = get_settings().cryptopanic.auth_token
        token = secret.get_secret_value() if secret is not None else None
    if not token:
        raise RuntimeError("CryptoPanic belum terkonfigurasi (SEITH_CRYPTOPANIC__AUTH_TOKEN)")
    sess = session or requests.Session()
    payload = get_json_retry(sess, CRYPTOPANIC_URL, {
        "auth_token": token,
        "currencies": currencies.upper(),
        "public": "true",
    })
    results = payload.get("results", []) if isinstance(payload, dict) else []
    items: list[NewsItem] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        published_at = _parse_published(entry.get("published_at"))
        title = str(entry.get("title", "")).strip()
        url = str(entry.get("url", "")).strip()
        external_id = str(entry.get("id", "")).strip()
        if published_at is None or not title or not url or not external_id:
            logger.debug("post cryptopanic tidak lengkap dilewati (id=%r)", entry.get("id"))
            continue
        codes = tuple(
            c.get("code", "").strip().upper()
            for c in entry.get("currencies") or []
            if isinstance(c, dict) and c.get("code")
        )
        positive, negative = _normalize_votes(entry.get("votes"))
        try:
            items.append(
                NewsItem(
                    external_id=external_id,
                    currencies=tuple(dict.fromkeys(codes)),
                    title=title,
                    url=url,
                    published_at=published_at,
                    positive_votes=positive,
                    negative_votes=negative,
                )
            )
        except ValidationError as exc:
            # baris rusak (judul >300 char dll.) dilewati, batch tetap jalan
            logger.debug("post cryptopanic tidak valid dilewati (%s)", exc.error_count())
    return items
