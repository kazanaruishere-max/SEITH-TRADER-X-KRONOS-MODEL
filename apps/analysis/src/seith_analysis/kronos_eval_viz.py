"""Visualisasi & analisis hasil Gate-A#2 (kronos_eval) — pure pandas/matplotlib.

Dipakai oleh notebook ``research/gate_a2_analysis.ipynb``. Semua fungsi murni
komputasi lokal; tidak ada network/GPU/LLM. Sumber data = artefak JSON formal
dari ``kronos_eval.main()`` (kolom detail: cutoff, pred_kronos,
pred_persistence, realized).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SUMMARY_KEYS = (
    "n_windows",
    "ic_kronos",
    "ic_persistence",
    "hit_kronos",
    "hit_persistence",
)


def _read_artifact(path: str | Path) -> dict[str, Any]:
    """Parses artefak eval: toleran terhadap baris log non-JSON sebelum body.

    Redirect shell (``*>``) menangkap log HF/download bersama JSON; ambil
    objek JSON pertama yang valid mulai dari ``{`` pertama.
    """

    text = Path(path).read_text(encoding="utf-8")
    start = text.find("{")
    if start == -1:
        raise ValueError(f"tidak ada JSON di {path}")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def load_detail(path: str | Path) -> pd.DataFrame:
    """Muat array ``detail`` dari artefak eval menjadi DataFrame ber-index cutoff."""
    df = pd.DataFrame(_read_artifact(path)["detail"])
    df["cutoff"] = pd.to_datetime(df["cutoff"], utc=True)
    return df.set_index("cutoff").sort_index()


def load_summary(path: str | Path) -> dict[str, Any]:
    """Muat metrik ringkasan (tanpa detail) dari artefak eval."""
    data = _read_artifact(path)
    return {k: data[k] for k in SUMMARY_KEYS}


def summary_table(summaries: dict[str, dict[str, Any]]) -> pd.DataFrame:
    """Gabungkan ringkasan beberapa pair menjadi satu tabel perbandingan."""
    return pd.DataFrame(summaries).T


def directional_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Return per-window mengikuti arah sinyal (+1 long / -1 short / 0 skip)."""
    out = pd.DataFrame(index=df.index)
    out["kronos"] = np.sign(df["pred_kronos"]) * df["realized"]
    out["persistence"] = np.sign(df["pred_persistence"]) * df["realized"]
    out["buy_hold"] = df["realized"]
    return out


def cumulative_pnl(df: pd.DataFrame) -> pd.DataFrame:
    """Kurva equity kumulatif dari strategi arah-sinyal vs buy-and-hold."""
    return directional_returns(df).cumsum()


def _spearman(a: pd.Series, b: pd.Series) -> float:
    if len(a) < 10 or a.nunique() < 2 or b.nunique() < 2:
        return float("nan")
    ic = a.corr(b, method="spearman")
    return float("nan") if pd.isna(ic) else float(ic)


def rolling_ic(df: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    """Spearman IC bergulir pred-vs-realized untuk Kronos dan persistence.

    Loop eksplisit per window — rolling.apply tidak bisa memakai DUA kolom
    sekaligus (ia mengirim tiap kolom terpisah sebagai Series).
    """
    rows_k: list[float] = []
    rows_p: list[float] = []
    index: list[pd.Timestamp] = []
    for end in range(window, len(df) + 1):
        sub = df.iloc[end - window : end]
        rows_k.append(_spearman(sub["pred_kronos"], sub["realized"]))
        rows_p.append(_spearman(sub["pred_persistence"], sub["realized"]))
        index.append(df.index[end - 1])
    return pd.DataFrame({"kronos": rows_k, "persistence": rows_p}, index=index)


def monthly_hit_rate(df: pd.DataFrame) -> pd.DataFrame:
    """Hit-rate arah per bulan kalender + jumlah window (uji konsistensi rezim)."""
    grouped = df.groupby(df.index.to_period("M"))
    rows = []
    for month, sub in grouped:
        rows.append(
            {
                "month": str(month),
                "n": len(sub),
                "hit_kronos": float(
                    (np.sign(sub["pred_kronos"]) == np.sign(sub["realized"])).mean()
                ),
                "hit_persistence": float(
                    (
                        np.sign(sub["pred_persistence"]) == np.sign(sub["realized"])
                    ).mean()
                ),
            }
        )
    return pd.DataFrame(rows).set_index("month")


def render_dashboard(
    df: pd.DataFrame,
    ticker: str,
    horizon_bars: int = 24,
    ic_window: int = 60,
    save_path: str | Path | None = None,
):
    """Dashboard 2x2: PnL kumulatif, scatter, rolling IC, hit-rate bulanan."""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    fig.suptitle(f"Gate-A#2 · {ticker} · h={horizon_bars} bar · n={len(df)}", fontsize=13)

    pnl = cumulative_pnl(df)
    axes[0][0].plot(pnl.index, pnl["kronos"], label="Kronos sign", lw=1.6)
    axes[0][0].plot(pnl.index, pnl["persistence"], label="Persistence sign", lw=1.2)
    axes[0][0].plot(pnl.index, pnl["buy_hold"], label="Buy & hold", lw=1.0, alpha=0.6)
    axes[0][0].set_title("Cumulative directional PnL (gross)")
    axes[0][0].axhline(0, color="gray", lw=0.5)
    axes[0][0].legend(fontsize=8)

    sample = df.sample(n=min(len(df), 400), random_state=7)
    axes[0][1].scatter(sample["pred_kronos"], sample["realized"], s=9, alpha=0.5)
    lim = max(abs(sample["realized"]).max(), abs(sample["pred_kronos"]).max()) * 1.05
    axes[0][1].plot([-lim, lim], [-lim, lim], color="gray", lw=0.6, ls="--")
    axes[0][1].set_title("Prediksi vs realized (Kronos)")
    axes[0][1].set_xlabel("pred")
    axes[0][1].set_ylabel("realized")

    ric = rolling_ic(df, ic_window)
    axes[1][0].plot(ric.index, ric["kronos"], label="Kronos", lw=1.4)
    axes[1][0].plot(ric.index, ric["persistence"], label="Persistence", lw=1.0)
    axes[1][0].axhline(0, color="gray", lw=0.5)
    axes[1][0].set_title(f"Rolling Spearman IC (window={ic_window})")
    axes[1][0].legend(fontsize=8)

    hr = monthly_hit_rate(df)
    x = np.arange(len(hr))
    axes[1][1].bar(x - 0.2, hr["hit_kronos"], width=0.4, label="Kronos")
    axes[1][1].bar(x + 0.2, hr["hit_persistence"], width=0.4, label="Persistence")
    axes[1][1].axhline(0.5, color="red", lw=0.8, ls=":")
    axes[1][1].set_xticks(x, hr.index, rotation=45, fontsize=7)
    axes[1][1].set_title("Hit-rate arah per bulan")
    axes[1][1].legend(fontsize=8)

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    if save_path is not None:
        fig.savefig(save_path, dpi=130)
    return fig
