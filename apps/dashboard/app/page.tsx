"use client";

import { useEffect, useState } from "react";

interface DecisionRow {
  decision_id: string;
  ticker: string;
  action: string;
  confidence: number;
  trade_date: string;
  reasoning_summary: string;
  created_at: string;
}

interface BacktestStats {
  ticker: string;
  timeframe: string;
  params: { fast: number; slow: number };
  outsample: {
    total_return: number;
    sharpe: number;
    max_drawdown: number;
    trades: number;
    win_rate: number;
  };
}

export default function Home() {
  const [decisions, setDecisions] = useState<DecisionRow[]>([]);
  const [backtests, setBacktests] = useState<BacktestStats[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [d, b] = await Promise.all([
          fetch("/api/decisions").then((r) => r.json()),
          fetch("/api/backtests").then((r) => r.json()),
        ]);
        setDecisions(d.decisions ?? []);
        setBacktests(b.backtests ?? []);
        setError(null);
      } catch {
        setError("Backend data belum tersedia (jalankan analisis dulu).");
      }
    };
    load();
    const t = setInterval(load, 5000); // realtime-lite; WS penuh di P6
    return () => clearInterval(t);
  }, []);

  return (
    <div className="container">
      <h1>
        SEITH <span>·</span> AI Hedge Fund
      </h1>
      <p className="subtitle">paper mode · kontrol via Telegram @SeithAI_bot · refresh 5s</p>

      {error && (
        <div className="grid">
          <div className="card empty">{error}</div>
        </div>
      )}

      <div className="grid">
        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>Keputusan Terakhir</h2>
          {decisions.length === 0 ? (
            <p className="empty">Belum ada — jalankan /analyze di Telegram.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Waktu</th>
                  <th>Ticker</th>
                  <th>Sinyal</th>
                  <th>Confidence</th>
                  <th>Alasan</th>
                </tr>
              </thead>
              <tbody>
                {decisions.map((d) => (
                  <tr key={d.decision_id}>
                    <td>{str(d.created_at).slice(0, 16).replace("T", " ")}</td>
                    <td>{d.ticker}</td>
                    <td>
                      <span className={`badge ${d.action}`}>{d.action.toUpperCase()}</span>
                    </td>
                    <td>{Math.round(d.confidence * 100)}%</td>
                    <td>{truncate(d.reasoning_summary, 120)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>Backtest (walk-forward, biaya realistis)</h2>
          {backtests.length === 0 ? (
            <p className="empty">
              Belum ada — jalankan: python -m seith_analysis.backtest --ticker BTCUSDT
            </p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Params SMA</th>
                  <th>Return OOS</th>
                  <th>Sharpe</th>
                  <th>Max DD</th>
                  <th>Trades</th>
                  <th>Tearsheet</th>
                </tr>
              </thead>
              <tbody>
                {backtests.map((b) => (
                  <tr key={`${b.ticker}-${b.timeframe}`}>
                    <td>{b.ticker} {b.timeframe}</td>
                    <td>{b.params.fast}/{b.params.slow}</td>
                    <td>{fmtPct(b.outsample.total_return)}</td>
                    <td>{b.outsample.sharpe.toFixed(2)}</td>
                    <td style={{ color: "var(--red)" }}>{fmtPct(-b.outsample.max_drawdown)}</td>
                    <td>{b.outsample.trades}</td>
                    <td>
                      <a
                        className="link"
                        href={`/api/tearsheet?ticker=${b.ticker}&tf=${b.timeframe}`}
                        target="_blank"
                      >
                        buka ↗
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <p className="footer">
        SEITH paper trading · bukan nasihat keuangan · approval gate manusia aktif
      </p>
    </div>
  );
}

function str(v: string) {
  return v ?? "";
}

function truncate(s: string, n: number) {
  if (!s) return "";
  const clean = s.replace(/\s+/g, " ");
  return clean.length > n ? clean.slice(0, n - 3) + "..." : clean;
}

function fmtPct(v: number) {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}
