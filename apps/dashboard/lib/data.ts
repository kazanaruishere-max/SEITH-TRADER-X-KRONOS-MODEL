import { readdirSync, readFileSync, existsSync } from "node:fs";
import path from "node:path";

/** Root repo SEITH - route handlers membaca artefak lokal langsung dari disk. */
export function repoRoot(): string {
  return process.env.SEITH_ROOT ?? path.resolve(process.cwd(), "../..");
}

export function dataDir(): string {
  return path.join(repoRoot(), "data");
}

export interface DecisionRow {
  decision_id: string;
  ticker: string;
  action: string;
  confidence: number;
  trade_date: string;
  reasoning_summary: string;
  created_at: string;
  forecast_id?: string;
}

export function listDecisions(limit = 20): DecisionRow[] {
  const dir = path.join(dataDir(), "decisions");
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => {
      try {
        return JSON.parse(readFileSync(path.join(dir, f), "utf-8")) as DecisionRow;
      } catch {
        return null;
      }
    })
    .filter((d): d is DecisionRow => d !== null)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
    .slice(0, limit);
}

export interface BacktestStats {
  ticker: string;
  timeframe: string;
  params: { fast: number; slow: number };
  outsample: { total_return: number; sharpe: number; max_drawdown: number; trades: number; win_rate: number };
  generated_at: string;
  tearsheet?: string;
}

export function listBacktests(): BacktestStats[] {
  const base = path.join(dataDir(), "backtests");
  if (!existsSync(base)) return [];
  const out: BacktestStats[] = [];
  for (const ticker of readdirSync(base)) {
    for (const tf of readdirSync(path.join(base, ticker))) {
      const statsPath = path.join(base, ticker, tf, "stats.json");
      if (existsSync(statsPath)) {
        try {
          out.push(JSON.parse(readFileSync(statsPath, "utf-8")) as BacktestStats);
        } catch {
          /* skip korup */
        }
      }
    }
  }
  return out.sort((a, b) => (a.generated_at < b.generated_at ? 1 : -1));
}

export function tearsheetHtml(ticker: string, timeframe: string): string | null {
  const p = path.join(dataDir(), "backtests", ticker.toUpperCase(), timeframe, "tearsheet.html");
  if (!existsSync(p)) return null;
  return readFileSync(p, "utf-8");
}
