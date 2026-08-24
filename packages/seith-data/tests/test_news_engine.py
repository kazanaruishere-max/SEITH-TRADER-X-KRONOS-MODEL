"""Test news engine E1: events store, kalender source, CryptoPanic, coverage m1."""

from datetime import UTC, date, datetime

import pandas as pd
import pytest
from seith_core.config import AppSettings
from seith_core.schemas import (
    AssetClass,
    EconomicEvent,
    EventImportance,
    NewsItem,
    Timeframe,
)

import seith_data.news_backfill as news_backfill
from seith_data.events_store import (
    count_economic_events,
    count_news_items,
    load_economic_events,
    load_news_items,
    upsert_economic_events,
    upsert_news_items,
)
from seith_data.m1_coverage import m1_coverage_report
from seith_data.sources.cryptopanic import _normalize_votes, fetch_cryptopanic_posts
from seith_data.sources.economic_calendar import (
    fetch_finnhub_calendar,
    fetch_forexfactory_week,
    map_importance,
    parse_number,
    slugify_event_type,
)
from seith_data.store import save_ohlcv


@pytest.fixture()
def settings(tmp_path):
    return AppSettings(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "test.db",
    )


def make_event(**overrides) -> EconomicEvent:
    defaults = dict(
        source_ref="finnhub:test:2026-09-04T12:30:00+00:00",
        source="finnhub",
        ticker="EUR_USD",
        asset_class=AssetClass.FOREX,
        event_type="non_farm_payrolls",
        importance=EventImportance.HIGH,
        currency="USD",
        scheduled_at=datetime(2026, 9, 4, 12, 30, tzinfo=UTC),
        forecast=185000.0,
    )
    defaults.update(overrides)
    return EconomicEvent(**defaults)


def make_item(**overrides) -> NewsItem:
    defaults = dict(
        external_id="100",
        currencies=("BTC",),
        title="Bitcoin ETF inflows record",
        url="https://cryptopanic.com/news/100",
        published_at=datetime(2026, 8, 20, 10, 0, tzinfo=UTC),
        positive_votes=5,
        negative_votes=1,
    )
    defaults.update(overrides)
    return NewsItem(**defaults)


class FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, json_data):
        self._json = json_data
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResponse(self._json)


class TestEventsStore:
    def test_upsert_load_round_trip(self, settings):
        event = make_event()
        upsert_economic_events([event], settings=settings)
        loaded = load_economic_events(settings=settings)
        assert loaded == [event]

    def test_natural_key_dedup_updates_not_duplicates(self, settings):
        upsert_economic_events([make_event()], settings=settings)
        updated = make_event(actual=175000.0)
        total_rows = upsert_economic_events([updated], settings=settings)
        assert total_rows == 1
        assert count_economic_events(settings=settings) == 1
        assert load_economic_events(settings=settings)[0].actual == 175000.0

    def test_filters_ticker_time_importance(self, settings):
        other = make_event(
            source_ref="finnhub:cpi:2026-09-10T12:30:00+00:00",
            event_type="cpi_us",
            ticker="AAPL",
            asset_class=AssetClass.EQUITY_US,
            scheduled_at=datetime(2026, 9, 10, 12, 30, tzinfo=UTC),
            importance=EventImportance.LOW,
        )
        upsert_economic_events([make_event(), other], settings=settings)

        by_ticker = load_economic_events(ticker="eur_usd", settings=settings)
        assert [e.event_type for e in by_ticker] == ["non_farm_payrolls"]

        by_window = load_economic_events(
            start=datetime(2026, 9, 5, tzinfo=UTC),
            end=datetime(2026, 9, 11, tzinfo=UTC),
            settings=settings,
        )
        assert [e.event_type for e in by_window] == ["cpi_us"]

        by_rank = load_economic_events(
            min_importance=EventImportance.MEDIUM, settings=settings
        )
        assert len(by_rank) == 1 and by_rank[0].importance == EventImportance.HIGH

    def test_news_dedup_by_external_id_and_currency_filter(self, settings):
        upsert_news_items([make_item()], settings=settings)
        upsert_news_items([make_item(positive_votes=9)], settings=settings)
        eth = make_item(external_id="200", currencies=("ETH", "BTC"))
        upsert_news_items([eth], settings=settings)

        assert count_news_items(settings=settings) == 2
        btc_only = load_news_items(currency="btc", settings=settings)
        assert len(btc_only) == 2
        latest = load_news_items(settings=settings)
        assert latest[0].external_id == "200"


FINNHUB_PAYLOAD = {
    "economicCalendar": [
        {
            "country": "US",
            "impact": "high",
            "event": "Non-Farm Payrolls",
            "time": "2026-09-04 12:30:00",
            "estimate": "185K",
            "prev": "216K",
            "actual": None,
        },
        {
            "country": "Euro Area",
            "impact": "medium",
            "event": "CPI Flash Estimate y/y",
            "time": "2026-09-01 09:00:00",
            "estimate": "2.3%",
        },
        {
            "country": "JP",  # tidak dipetakan -> dilewati
            "impact": "high",
            "event": "BoJ Rate Decision",
            "time": "2026-09-18 03:00:00",
        },
        {"country": "US", "event": "Bad Row No Time", "time": ""},
    ]
}

FF_PAYLOAD = [
    {
        "title": "Non-Farm Employment Change",
        "country": "USD",
        "date": "2026-09-04T08:30:00-04:00",
        "impact": "High",
        "forecast": "185K",
        "previous": "216K",
    }
]


class TestEconomicCalendarSource:
    def test_fetch_finnhub_maps_and_skips(self):
        session = FakeSession(FINNHUB_PAYLOAD)
        events = fetch_finnhub_calendar(
            date(2026, 9, 1), date(2026, 9, 30), api_key="tok", session=session
        )
        # USD dipetakan ke 5 target, EU ke 1, JP & baris rusak dilewati;
        # "Non-Farm Payrolls" dikanonikasi -> nonfarm_payrolls (satu bucket
        # dengan FRED Employment Situation)
        assert len(events) == 6
        nfp = [e for e in events if e.event_type == "nonfarm_payrolls"]
        assert {e.ticker for e in nfp} == {"EUR_USD", "BTCUSDT", "ETHUSDT", "AAPL", "NVDA"}
        eur = next(e for e in events if e.currency == "EUR")
        assert eur.forecast == pytest.approx(2.3)
        assert all(e.source == "finnhub" for e in events)
        url, params = session.calls[0]
        assert params["token"] == "tok"
        assert url.endswith("/calendar/economic")

    def test_fetch_finhhub_requires_token(self):
        with pytest.raises(RuntimeError, match="Finnhub"):
            fetch_finnhub_calendar(date(2026, 9, 1), date(2026, 9, 2))

    def test_fetch_forexfactory_week_parses_offset(self):
        events = fetch_forexfactory_week(session=FakeSession(FF_PAYLOAD))
        assert len(events) == 5  # USD -> EUR_USD, BTC, ETH, AAPL, NVDA
        assert {e.currency for e in events} == {"USD"}
        assert all(e.scheduled_at.utcoffset() == UTC.utcoffset(None) for e in events)


class TestParsers:
    def test_slugify(self):
        assert slugify_event_type("Non-Farm Payrolls") == "non_farm_payrolls"
        assert slugify_event_type("  CPI  y/y ") == "cpi_y_y"

    def test_parse_number(self):
        assert parse_number("185K") == 185000.0
        assert parse_number("2.4%") == pytest.approx(2.4)
        assert parse_number("1,234") == 1234.0
        assert parse_number("-0.5") == -0.5
        assert parse_number("") is None
        assert parse_number(None) is None

    def test_parse_number_rejects_non_finite(self):
        assert parse_number("NaN") is None
        assert parse_number("Infinity") is None
        assert parse_number(float("nan")) is None
        assert parse_number(float("inf")) is None

    def test_map_importance_unknown_returns_none(self):
        # fallback MEDIUM = misklasifikasi senyap yang mencemari trigger E3
        assert map_importance("HIGH") == EventImportance.HIGH
        assert map_importance("Holiday") is None

    def test_malformed_row_does_not_abort_batch(self):
        payload = {
            "economicCalendar": [
                {"country": "US", "impact": "high", "event": "   ",
                 "time": "2026-09-04 12:30:00"},  # judul kosong -> skip
                {"country": "EUR", "impact": "Holiday",
                 "time": "2026-09-05 09:00:00"},  # impact tak dikenal -> skip
                FINNHUB_PAYLOAD["economicCalendar"][1],  # baris valid EU CPI
            ]
        }
        events = fetch_finnhub_calendar(
            date(2026, 9, 1), date(2026, 9, 30), api_key="tok",
            session=FakeSession(payload),
        )
        assert len(events) == 1
        assert events[0].currency == "EUR"

    def test_forexfactory_naive_date_treated_as_utc_not_local(self):
        payload = [dict(FF_PAYLOAD[0], date="2026-09-04T08:30:00")]
        events = fetch_forexfactory_week(session=FakeSession(payload))
        assert len(events) == 5  # USD -> EUR_USD, BTC, ETH, AAPL, NVDA
        assert all(e.scheduled_at.hour == 8 for e in events)

    def test_non_transient_status_not_retried(self):
        import requests as requests_lib

        from seith_data.sources.economic_calendar import CalendarSourceError

        class ForbiddenSession:
            calls = 0

            def get(self, url, params=None, timeout=None):
                ForbiddenSession.calls += 1

                class Resp:
                    status_code = 401

                    def raise_for_status(self):
                        err = requests_lib.HTTPError("401 Client Error")
                        err.response = self  # kode audit membaca exc.response.status_code
                        raise err

                    def json(self):
                        return {}

                return Resp()

        with pytest.raises(CalendarSourceError):
            fetch_finnhub_calendar(
                date(2026, 9, 1), date(2026, 9, 2),
                api_key="tok", session=ForbiddenSession(),
            )
        assert ForbiddenSession.calls == 1


CRYPTOPANIC_PAYLOAD = {
    "results": [
        {
            "id": 777,
            "title": "Ethereum staking surges",
            "url": "https://cryptopanic.com/news/777",
            "published_at": "2026-08-21T05:00:00Z",
            "votes": {"positive": 12, "negative": 3},
            "currencies": [{"code": "ETH"}, {"code": "eth"}, {"code": "BTC"}],
        },
        {"id": 888, "title": "", "url": "", "published_at": ""},  # rusak -> dilewati
    ]
}


class TestCryptoPanicSource:
    def test_normalize_votes_variants(self):
        assert _normalize_votes({"positive": 2, "negative": 1}) == (2, 1)
        assert _normalize_votes({}) == (0, 0)
        assert _normalize_votes({"positive": None}) == (0, 0)
        assert _normalize_votes(None) == (0, 0)

    def test_fetch_posts_validates_and_normalizes(self):
        items = fetch_cryptopanic_posts(
            auth_token="tok", session=FakeSession(CRYPTOPANIC_PAYLOAD)
        )
        assert len(items) == 1
        item = items[0]
        assert item.external_id == "777"
        assert item.currencies == ("ETH", "BTC")  # dedup + uppercase
        assert item.published_at.tzinfo is not None
        assert item.positive_votes == 12
        assert item.sentiment.value == "positive"

    def test_fetch_requires_token(self):
        with pytest.raises(RuntimeError, match="CryptoPanic"):
            fetch_cryptopanic_posts(session=FakeSession(CRYPTOPANIC_PAYLOAD))


class TestM1Coverage:
    def test_empty_store_reports_missing(self, settings):
        reports = m1_coverage_report(["BTCUSDT"], settings=settings)
        report = reports[0]
        assert report.exists is False
        assert report.bar_count == 0
        assert report.coverage_pct == 0.0

    def test_full_coverage_one_minute_bars(self, settings):
        idx = pd.date_range("2026-08-01T10:00:00Z", periods=60, freq="1min")
        df = pd.DataFrame(
            {
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
            },
            index=pd.DatetimeIndex(idx, name="timestamp"),
        ).astype("float64")
        save_ohlcv(df, "EUR_USD", Timeframe.M1, settings)
        (report,) = m1_coverage_report(["EUR_USD"], settings=settings)
        assert report.exists is True
        assert report.bar_count == 60
        assert report.expected_bars == 60
        assert report.coverage_pct == 100.0


class TestNewsBackfill:
    def test_month_chunks_span_boundary(self):
        chunks = news_backfill._month_chunks(date(2026, 7, 15), date(2026, 9, 10))
        assert chunks[0] == (date(2026, 7, 15), date(2026, 7, 31))
        assert chunks[1] == (date(2026, 8, 1), date(2026, 8, 31))
        assert chunks[-1] == (date(2026, 9, 1), date(2026, 9, 10))

    def test_backfill_writes_to_store(self, settings, monkeypatch):
        captured: list[tuple[date, date]] = []

        def fake_fetch(start, end, api_key=None):
            captured.append((start, end))
            return [
                make_event(
                    source_ref=f"finnhub:{start.isoformat()}",
                    scheduled_at=datetime(2026, start.month, 4, 12, 30, tzinfo=UTC),
                )
            ]

        monkeypatch.setattr(news_backfill, "fetch_finnhub_calendar", fake_fetch)
        written = news_backfill.backfill_economic_events(
            date(2026, 7, 1), date(2026, 9, 1), api_key="k", settings=settings
        )
        assert written == 3
        assert len(captured) == 3
        assert count_economic_events(settings=settings) == 3
