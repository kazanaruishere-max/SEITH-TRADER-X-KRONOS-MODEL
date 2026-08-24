"""Fallback keyless berita crypto via RSS publik (E1 MATA).

Sumber: CoinDesk + Cointelegraph (daftar RSS_FEEDS, mudah ditambah).
Tanpa API key sama sekali - stdlib xml.etree + requests. Sentimen netral
(votes 0) sampai analisis sentimen sendiri tersedia; konsumen E5/E6 yang
memutuskan apakah itu cukup.

external_id = 'rss:<source>:<sha1(guid|link)[:16]>' agar stabil & unik.
"""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import requests
from pydantic import ValidationError
from seith_core.schemas import NewsItem

logger = logging.getLogger(__name__)

RSS_FEEDS: dict[str, str] = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
}

_TIMEOUT_SECONDS = 30


def _stable_id(source: str, guid: str | None, link: str | None) -> str | None:
    basis = (guid or link or "").strip()
    if not basis:
        return None
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    return f"rss:{source}:{digest}"


def _parse_pub_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError):
        logger.debug("pubDate tidak valid dilewati: %r", raw[:32])
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_feed(xml_text: str, source: str) -> list[NewsItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("RSS '%s' gagal diparse: %s", source, exc)
        return []
    items: list[NewsItem] = []
    for node in root.iter("item"):
        title_el = node.findtext("title")
        link_el = node.findtext("link")
        guid_el = node.findtext("guid")
        title = _strip_html(title_el or "")
        url = (link_el or "").strip()
        published_at = _parse_pub_date(node.findtext("pubDate"))
        external_id = _stable_id(source, guid_el, url)
        if not title or not url or published_at is None or external_id is None:
            continue
        if len(title) > 300:
            title = title[:297] + "..."
        try:
            items.append(
                NewsItem(
                    external_id=external_id,
                    currencies=(),
                    title=title,
                    url=url,
                    published_at=published_at,
                )
            )
        except ValidationError as exc:
            logger.debug("item rss '%s' tidak valid dilewati (%s)", source, exc.error_count())
    return items


def fetch_rss_news(
    feeds: dict[str, str] | None = None,
    session: requests.Session | None = None,
) -> list[NewsItem]:
    """Ambil semua feed RSS terkonfigurasi; satu feed gagal TIDAK menggugurkan lain."""
    active = feeds or RSS_FEEDS
    sess = session or requests.Session()
    all_items: list[NewsItem] = []
    for source, feed_url in sorted(active.items()):
        try:
            resp = sess.get(feed_url, timeout=_TIMEOUT_SECONDS)
            resp.raise_for_status()
        except requests.RequestException as exc:
            # log tanpa body exception utk hindari bocor URL internal bila ada
            logger.warning("RSS '%s' gagal diambil (%s)", source, type(exc).__name__)
            continue
        fetched = _parse_feed(resp.text, source)
        logger.info("rss '%s': %d item", source, len(fetched))
        all_items.extend(fetched)
    return all_items
