"""Test sumber berita & kalender baru: CoinDesk (CCData), RSS keyless, FRED."""

from datetime import date

import pytest
from seith_core.config import AppSettings
from seith_core.schemas import EventImportance

# reuse FakeSession dari test_news_engine
from test_news_engine import FakeSession

from seith_data.sources.coindesk_news import fetch_coindesk_articles
from seith_data.sources.fred_calendar import (
    FRED_RELEASE_SPECS,
    FredReleaseSpec,
    canonical_event_type,
    fetch_fred_calendar,
    fetch_fred_release_dates,
)
from seith_data.sources.rss_news import fetch_rss_news


@pytest.fixture()
def settings(tmp_path):
    return AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "test.db",
    )


CCDATA_PAYLOAD = {
    "Data": [
        {
            "ID": 5550001,
            "TITLE": "Bitcoin ETF inflows accelerate",
            "URL": "https://coindesk.com/markets/etf-inflows",
            "PUBLISHED_ON": 1787565645,
            "UPVOTES": 12,
            "DOWNVOTES": 3,
            "COINS": ["BTC", "btc", "ETH"],
        },
        {"ID": 5550002, "TITLE": "", "URL": "", "PUBLISHED_ON": 1787560000},  # rusak
        {
            "ID": 5550003,
            "TITLE": "x" * 400,  # judul kepanjangan -> ValidationError -> skip
            "URL": "https://coindesk.com/x",
            "PUBLISHED_ON": 1787565700,
        },
    ]
}


class TestCoinDeskNews:
    def test_maps_fields_and_namespaces_external_id(self):
        items = fetch_coindesk_articles(api_key="tok", session=FakeSession(CCDATA_PAYLOAD))
        assert len(items) == 1
        item = items[0]
        assert item.external_id == "cd:5550001"
        assert item.title == "Bitcoin ETF inflows accelerate"
        assert item.currencies == ("BTC", "ETH")  # dedup + uppercase
        assert item.published_at.tzinfo is not None
        assert item.positive_votes == 12 and item.negative_votes == 3
        assert item.sentiment.value == "positive"

    def test_requires_token(self):
        with pytest.raises(RuntimeError, match="CoinDesk"):
            fetch_coindesk_articles(session=FakeSession(CCDATA_PAYLOAD))


RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
  <title>Altcoin rally &lt;b&gt;continues&lt;/b&gt;</title>
  <link>https://cointelegraph.com/news/rally</link>
  <guid>cointelegraph-99</guid>
  <pubDate>Mon, 24 Aug 2026 09:30:00 +0000</pubDate>
</item>
<item>
  <title>No date item</title>
  <link>https://cointelegraph.com/news/nodate</link>
</item>
</channel></rss>"""


class TestRssNews:
    def test_parses_items_and_strips_html(self):
        class RssSession:
            def get(self, url, timeout=None):
                class Resp:
                    text = RSS_XML

                    def raise_for_status(self):
                        return None

                return Resp()

        items = fetch_rss_news(feeds={"cointelegraph": "http://x/rss"}, session=RssSession())
        assert len(items) == 1  # item tanpa pubDate dilewati
        item = items[0]
        assert "<b>" not in item.title and "continues" in item.title
        assert item.external_id.startswith("rss:cointelegraph:")
        assert item.published_at.year == 2026 and item.published_at.hour == 9
        assert item.sentiment.value == "neutral"  # tanpa votes

    def test_stable_id_same_guid(self):
        from seith_data.sources.rss_news import _stable_id

        assert _stable_id("s", "guid-1", "http://a") == _stable_id("s", "guid-1", "http://a")
        assert _stable_id("s", "guid-1", "http://a") != _stable_id("s", "guid-2", "http://a")


class TestCanonicalEventType:
    def test_provider_vocabularies_merge_to_one_bucket(self):
        assert canonical_event_type("non_farm_payrolls") == "nonfarm_payrolls"
        assert canonical_event_type("non-farm-employment-change") == "nonfarm_payrolls"
        assert canonical_event_type("employment_situation") == "nonfarm_payrolls"
        assert canonical_event_type("cpi_m_m") == "cpi"
        assert canonical_event_type("core_cpi_y_y") == "cpi"
        assert canonical_event_type("core_pce_price_index_m_m") == "pce"
        assert canonical_event_type("prelim_gdp_q_q") == "gdp"
        assert canonical_event_type("retail_sales_m_m") == "retail_sales"
        assert canonical_event_type("initial_jobless_claims_w_w") == "jobless_claims"

    def test_unknown_passes_through(self):
        assert canonical_event_type("fed_chairman_warsh_speaks") == (
            "fed_chairman_warsh_speaks"
        )


FRED_PAYLOAD = {
    "release_dates": [
        {"date": "2026-07-02", "release_id": 180},   # di luar range
        {"date": "2026-08-06", "release_id": 180},
        {"date": "2026-08-13", "release_id": 180},
        {"date": "bad-date", "release_id": 180},
    ]
}


class TestFredCalendar:
    def test_release_dates_filtered_by_range(self):
        spec = FredReleaseSpec(180, "jobless_claims", EventImportance.MEDIUM)
        dates = fetch_fred_release_dates(
            spec, date(2026, 8, 1), date(2026, 8, 20),
            api_key="tok", session=FakeSession(FRED_PAYLOAD),
        )
        assert dates == [date(2026, 8, 6), date(2026, 8, 13)]

    def test_calendar_builds_utc_events_with_dst(self):
        events = fetch_fred_calendar(
            date(2026, 8, 1), date(2026, 8, 20),
            api_key="tok", session=FakeSession(FRED_PAYLOAD),
            specs=(FRED_RELEASE_SPECS[0],),  # jobless_claims 08:30 ET
        )
        # 2 tanggal x 5 ticker USD target
        assert len(events) == 10
        aug6 = [e for e in events if e.scheduled_at.day == 6][0]
        # Agustus = EDT (UTC-4) -> 08:30 NY = 12:30 UTC
        assert aug6.scheduled_at.utcoffset().total_seconds() == 0
        assert (aug6.scheduled_at.hour, aug6.scheduled_at.minute) == (12, 30)
        assert aug6.event_type == "jobless_claims"
        assert aug6.source == "fred" and aug6.currency == "USD"
        assert {e.ticker for e in events} == {
            "EUR_USD", "BTCUSDT", "ETHUSDT", "AAPL", "NVDA",
        }

    def test_natural_keys_distinct_across_dates(self, settings):
        events = fetch_fred_calendar(
            date(2026, 8, 1), date(2026, 8, 20),
            api_key="tok", session=FakeSession(FRED_PAYLOAD),
            specs=(FRED_RELEASE_SPECS[0],),
        )
        keys = {(e.ticker, e.event_type, e.scheduled_at) for e in events}
        assert len(keys) == 10

    def test_naive_local_hhmm_rejected(self):
        bad_spec = FredReleaseSpec(1, "x", EventImportance.LOW, local_hhmm="8:30")
        with pytest.raises(ValueError, match="local_hhmm"):
            fetch_fred_calendar(
                date(2026, 8, 1), date(2026, 8, 2),
                api_key="tok", session=FakeSession({"release_dates": []}),
                specs=(bad_spec,),
            )

    def test_specs_use_canonical_slugs_and_valid_times(self):
        from seith_data.sources.fred_calendar import _parse_local_hhmm

        for spec in FRED_RELEASE_SPECS:
            # slug di spec WAJIB sudah kanonik (idempotent) agar bucket konsisten
            assert canonical_event_type(spec.event_type) == spec.event_type
            hour, minute = _parse_local_hhmm(spec.local_hhmm)
            assert 0 <= hour <= 23 and 0 <= minute <= 59
