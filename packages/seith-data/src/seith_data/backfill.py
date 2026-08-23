"""Backfill OHLCV ke Parquet + metadata SQLite. Entry: python -m seith_data.backfill."""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

from seith_core.schemas import Timeframe

from seith_data.quality import run_checks
from seith_data.sources import binance, detect_source, oanda, yf
from seith_data.store import finish_run, record_findings, start_run, upsert_ohlcv

logger = logging.getLogger("seith.data")

_SOURCES = {"binance": binance.fetch, "oanda": oanda.fetch, "yfinance": yf.fetch}


def backfill(
    ticker: str,
    timeframe: str,
    days: int,
    source: str | None = None,
) -> dict[str, object]:
    tf = Timeframe(timeframe)
    src = (source or detect_source(ticker)).lower()
    if src not in _SOURCES:
        raise ValueError(f"source '{src}' tidak dikenal; pilih dari {sorted(_SOURCES)}")
    end = datetime.now(UTC)
    start = end - timedelta(days=days)

    run_id = start_run(ticker, tf, src)
    logger.info("[%s] %s %s mulai (%d hari)", src, ticker.upper(), tf.value, days)
    try:
        fetch = _SOURCES[src]
        df = fetch(ticker.upper(), tf, start, end)
        df.attrs["timeframe"] = tf.value
        total_rows = upsert_ohlcv(df, ticker, tf)
        findings = run_checks(df, src)
        record_findings(run_id, ticker, tf, findings)
        finish_run(run_id, total_rows)
        logger.info(
            "[%s] %s selesai: +%d bar baru (total %d), %d temuan kualitas",
            src,
            ticker.upper(),
            len(df),
            total_rows,
            len(findings),
        )
        return {
            "ticker": ticker.upper(),
            "source": src,
            "timeframe": tf.value,
            "rows_fetched": len(df),
            "rows_total": total_rows,
            "quality_findings": findings,
        }
    except Exception as exc:
        finish_run(run_id, 0, error=str(exc))
        logger.exception("backfill gagal untuk %s", ticker)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="SEITH OHLCV backfill")
    parser.add_argument("--tickers", required=True, help="daftar dipisah koma, mis BTCUSDT,EUR_USD")
    parser.add_argument("--timeframe", default="1h", help="1m|5m|15m|1h|4h|1d")
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--source", default=None, choices=["binance", "oanda", "yfinance"])
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days wajib positif")
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")

    failures: list[tuple[str, str]] = []
    for ticker in [t.strip() for t in args.tickers.split(",") if t.strip()]:
        try:
            result = backfill(ticker, args.timeframe, args.days, args.source)
            print(
                f"{result['ticker']:>10} {result['timeframe']:>3} via {result['source']}: "
                f"+{result['rows_fetched']} bar / total {result['rows_total']}"
            )
        except Exception as exc:  # noqa: BLE001 - satu ticker gagal tak boleh hentikan batch
            failures.append((ticker, str(exc)))
            print(f"{ticker:>10} GAGAL: {exc}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
