"""Berita crypto via CoinDesk Data / CCData (primer E1 MATA, key gratis CCData).

Endpoint: GET https://data-api.coindesk.com/news/v1/article/list?api_key=...
Field respons dipetakan ke NewsItem: ID -> external_id (dengan namespace 'cd:'
agar tak bentrok id numeric CryptoPanic di store yang sama), PUBLISHED_ON
(epoch detik) -> published_at UTC, UPVOTES/DOWNVOTES -> votes (sentimen
terhitung otomatis oleh schema).
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

COINDESK_NEWS_URL = "https://data-api.coindesk.com/news/v1/article/list"


def _epoch_to_utc(raw: object) -> datetime | None:
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=UTC)
    except (OSError, OverflowError, ValueError):
        logger.debug("PUBLISHED_ON tidak valid dilewati: %r", raw)
        return None


def fetch_coindesk_articles(
    api_key: str | None = None,
    limit: int = 50,
    session: requests.Session | None = None,
) -> list[NewsItem]:
    """Ambil artikel berita terbaru CoinDesk Data; return NewsItem tervalidasi."""
    token = api_key
    if not token:
        secret = get_settings().coindesk.api_key
        token = secret.get_secret_value() if secret is not None else None
    if not token:
        raise RuntimeError("CoinDesk belum terkonfigurasi (SEITH_COINDESK__API_KEY)")
    sess = session or requests.Session()
    payload = get_json_retry(sess, COINDESK_NEWS_URL, {
        "api_key": token,
        "limit": str(int(limit)),
    })
    results = payload.get("Data", []) if isinstance(payload, dict) else []
    items: list[NewsItem] = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        published_at = _epoch_to_utc(entry.get("PUBLISHED_ON"))
        title = str(entry.get("TITLE", "")).strip()
        url = str(entry.get("URL", "")).strip()
        article_id = str(entry.get("ID", "")).strip()
        if published_at is None or not title or not url or not article_id:
            logger.debug("artikel coindesk tidak lengkap dilewati (id=%r)", entry.get("ID"))
            continue
        codes = tuple(
            c.strip().upper()
            for c in entry.get("COINS") or []
            if isinstance(c, str) and c.strip()
        )
        votes = entry.get("UPVOTES", 0), entry.get("DOWNVOTES", 0)
        try:
            positive, negative = max(0, int(votes[0])), max(0, int(votes[1]))
        except (TypeError, ValueError):
            positive = negative = 0
        try:
            items.append(
                NewsItem(
                    external_id=f"cd:{article_id}",
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
            logger.debug("artikel coindesk tidak valid dilewati (%s)", exc.error_count())
    return items
