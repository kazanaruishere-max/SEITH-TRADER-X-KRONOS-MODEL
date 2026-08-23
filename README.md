# SEITH - Personal AI Hedge Fund Platform

> **Agent harness?** Baca [`AGENTS.md`](AGENTS.md) sebelum menulis kode.

Platform trading personal berbasis AI multi-agent dengan workflow hedge fund:
analisis multi-market (crypto, saham US, forex), prediksi harga foundation-model,
backtesting vektorisasi, dan eksekusi paper-trading production-grade.

## Arsitektur

```
Telegram (aiogram) ─┐                    ┌─ Dashboard (Next.js + WS)
                    ▼                    ▼
              [ Core API - FastAPI ]  <- REST + WebSocket
                    |
      +-------------+---------------+
      v             v               v
 Analysis Svc   vectorbt       Trader Node
 TradingAgents  (backtest      nautilus_trader:
 + Kronos GPU   + tearsheet)   SignalActor -> RiskMgr -> SandboxExec -> Binance feed
```

## Struktur Monorepo

| Path | Isi |
|---|---|
| `apps/api` | Core API FastAPI + Telegram bot (orchestrator hub) |
| `apps/analysis` | TradingAgents (Groq) + Kronos inference + backtest runner |
| `apps/trader` | nautilus_trader node: signal intake, risk manager, sandbox execution |
| `packages/seith-core` | Shared domain schemas (Pydantic) + config/secrets loader |
| `vendor/` | Fork Kronos & TradingAgents untuk customisasi |
| `research/` | Notebook eksperimen vectorbt & fine-tune Kronos |
| `docs/` | PRD, ADR, runbook |

## Setup

Prasyarat: [uv](https://docs.astral.sh/uv/) terpasang, git, GPU NVIDIA + driver CUDA (untuk Kronos).

```powershell
# Sinkronisasi semua environment (per-app venv otomatis dari uv workspace)
uv sync --project packages/seith-core
uv sync --project apps/api
uv sync --project apps/trader
uv sync --project apps/analysis

# Jalankan tests seith-core
uv run --project packages/seith-core pytest
```

Copy `.env.example` ke `.env`, isi secret sesuai kebutuhan fase.

## Keamanan

- Secret hanya via `.env` / keyring - tidak pernah masuk kode atau log.
- Saat live nanti: API key Binance wajib **trade-only** (tanpa izin withdrawal) + IP whitelist.
- Fase paper tidak membutuhkan key trading sama sekali.

## Dokumentasi

- `docs/PRD.md` - Product Requirements Document
- `docs/adr/` - Architecture Decision Records
