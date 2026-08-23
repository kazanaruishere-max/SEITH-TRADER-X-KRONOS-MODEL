"""Data quality checks untuk OHLCV DataFrame.

Severity: 'warn' = perlu diperhatikan, 'info' = catatan saja.
Gap di market ber-sesi (saham/forex weekend) adalah normal - dilaporkan info.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from seith_core.schemas import Timeframe

from seith_data.timeutil import timeframe_seconds


def run_checks(df: pd.DataFrame, source: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if df.empty:
        return [{"check_name": "empty_frame", "severity": "warn", "detail": "0 baris"}]
    if not isinstance(df.attrs.get("timeframe"), str):
        raise ValueError("run_checks butuh df.attrs['timeframe'] (str) - inject dulu")

    dupes = int(df.index.duplicated(keep=False).sum())
    if dupes:
        findings.append(
            {"check_name": "duplicate_timestamps", "severity": "warn", "detail": f"{dupes} baris"}
        )

    if not df.index.is_monotonic_increasing:
        findings.append(
            {"check_name": "index_not_sorted", "severity": "warn", "detail": "index tak monotonik"}
        )

    diffs = df.index.to_series().diff().dropna()
    expected = pd.Timedelta(seconds=timeframe_seconds(Timeframe(df.attrs["timeframe"])))
    big_gaps = diffs[diffs > expected * 3]
    if len(big_gaps):
        worst = big_gaps.max()
        detail = f"{len(big_gaps)} gap >3x cadence; terbesar {worst}" + (
            " (normal untuk market ber-sesi)" if source != "binance" else ""
        )
        findings.append(
            {
                "check_name": "cadence_gaps",
                "severity": "info" if source != "binance" else "warn",
                "detail": detail,
            }
        )

    ret = df["close"].pct_change()
    outliers = ret[ret.abs() > 0.20]
    if len(outliers):
        findings.append(
            {
                "check_name": "outlier_returns_gt_20pct",
                "severity": "info",
                "detail": f"{len(outliers)} bar; max {ret.abs().max():.2%}",
            }
        )
    return findings
