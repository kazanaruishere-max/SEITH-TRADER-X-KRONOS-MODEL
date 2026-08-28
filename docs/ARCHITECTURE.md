# Arsitektur SEITH — AI Hedge Fund Platform

> **Status:** Paper trading penuh — live trading terkunci di balik gerbang go-live (lihat PRD §8)

---

## 1. Gambaran Umum

SEITH = personal AI hedge fund platform:
- **Analisis multi-agent LLM** (TradingAgents + 9router/Groq)
- **Prediksi harga foundation-model** (Kronos-base, GPU lokal RTX 4050)
- **Backtest vectorbt-style** (walk-forward, anti-lookahead)
- **Eksekusi paper-trading production-grade** (nautilus_trader)
- **Antarmuka**: Telegram bot (@SeithAI_bot) + Dashboard web (Next.js 15 + WS)

---

## 2. Diagram Arsitektur End-to-End

```mermaid
flowchart TD
    %% Data Layer
    subgraph Data["📊 Data Layer"]
        BINANCE[(Binance REST/WS)]
        OANDA[(OANDA v20)]
        YFINANCE[(yfinance)]
        BINANCE --> BACKFILL[Backfill Engine]
        OANDA --> BACKFILL
        YFINANCE --> BACKFILL
        BACKFILL --> PARQUET[(Parquet Files)]
        BACKFILL --> SQLITE[(SQLite DB)]
    end

    %% Analysis Layer
    subgraph Analysis["🧠 Analysis Layer"]
        PARQUET --> KRONOS[Kronos Forecast GPU]
        SQLITE --> KRONOS
        KRONOS --> TRADINGAGENTS[TradingAgents Debate]
        TRADINGAGENTS --> DECISION[Decision JSON]
    end

    %% Execution Layer
    subgraph Execution["⚙️ Execution Layer"]
        DECISION --> PROPOSAL[OrderProposal + approved_by]
        PROPOSAL --> RISK[RiskManager Check]
        RISK --> INTAKE[Intake Loop]
        INTAKE --> NAUTILUS[nautilus_trader Sandbox]
        NAUTILUS --> FILL[Fill → PnL Snapshot]
    end

    %% Interfaces
    subgraph Interfaces["🌐 Interfaces"]
        FILL --> HUB[FastAPI Hub + WS]
        HUB --> DASHBOARD[Next.js Dashboard]
        HUB --> BOT[Telegram Bot]
        BOT --> OWNER[Owner Approve/Reject]
        OWNER --> PROPOSAL
    end

    %% Styling
    classDef data fill:#e3f2fd,stroke:#1565c0
    classDef analysis fill:#f3e5f5,stroke:#7b1fa2
    classDef execution fill:#e8f5e9,stroke:#2e7d32
    classDef interfaces fill:#fff3e0,stroke:#ef6c00
    class Data,Analysis,Execution,Interfaces fill:transparent,stroke-dasharray: 5 5
```

---

## 3. Komponen Per Layanan

| Layanan | Path | Deskripsi | Dependensi |
|---|---|---|---|
| **Data Ingestion** | `packages/seith-data` | Backfill Binance/OANDA/yfinance → Parquet + SQLite | ccxt, oanda-api, yfinance |
| **Analysis Engine** | `apps/analysis` | Kronos forecast + TradingAgents + backtest + eval | Kronos-base, TradingAgents, vectorbt |
| **Trader/Execution** | `apps/trader` | nautilus_trader node: SignalActor → RiskMgr → SandboxExec | nautilus_trader, SQLite |
| **API/Hub/Bot** | `apps/api` | FastAPI hub + Telegram bot + WebSocket | FastAPI, aiogram, 9router |
| **Dashboard** | `apps/dashboard` | Next.js 15 + WebSocket real-time | Next.js, React, Tailwind |
| **Core Schemas** | `packages/seith-core` | Domain models (Pydantic frozen, extra=forbid) | Pydantic, Python 3.12 |
| **Vendor** | `vendor/Kronos`, `vendor/TradingAgents` | Fork ter-pin (ADR-0002) | Pin commit hash |

---

## 4. Kontrak Data (Schema Utama)

Semua model domain didefinisikan di `packages/seith-core/src/seith_core/schemas.py`:

| Model | Keterangan |
|---|---|
| `Signal` | Sinyal trading dari analisis |
| `Decision` | Keputusan terstandar dari TradingAgents |
| `OrderProposal` | Proposal order dengan invariant `approved_by` wajib |
| `ForecastResult` | Hasil prediksi Kronos (expected_return, confidence) |
| `PositionSnapshot` | Snapshot posisi dari nautilus |
| `RiskLimits` | Limit risiko (max position, daily loss, drawdown) |
| `Ticker` / `Timeframe` / `OrderType` | Enum terstandar |
| `AwareDatetime` | Timestamp UTC-aware wajib |

**Invariant kunci:** `OrderProposal` status `APPROVED/SUBMITTED/FILLED` wajib punya `approved_by`. Trader node **tidak percaya** field `status` dari wire — verifikasi ulang ke catatan sendiri.

---

## 5. Environment & Konfigurasi

### Environment Variables (`.env` — gitignored)

| Prefix | Deskripsi | Contoh |
|---|---|---|
| `SEITH_LLM__*` | LLM provider, model, API key | `PROVIDER=openrouter`, `QUICK_MODEL=...` |
| `SEITH_TELEGRAM__*` | Bot token, allowlist, channel | `BOT_TOKEN=...`, `ALLOWED_USER_IDS=[...]` |
| `SEITH_BINANCE__*` | Binance API (paper: kosong) | `API_KEY=...`, `API_SECRET=...` |
| `SEITH_OANDA__*` | OANDA v20 practice | `ACCESS_TOKEN=...`, `ACCOUNT_ID=...` |
| `SEITH_KRONOS__*` | Kronos config | `MODEL_NAME=NeoQuasar/Kronos-base` |
| `SEITH_RISK__*` | Risk limits default | `MAX_POSITION_PCT=0.10` |
| `SEITH_DATA_DIR`, `SEITH_DB_PATH` | Path absolut wajib | `C:\Users\...\data` |

### File Konfigurasi Utama

| File | Deskripsi |
|---|---|
| `packages/seith-core/src/seith_core/config.py` | Pydantic Settings (fail-fast unknown env) |
| `packages/seith-core/src/seith_core/schemas.py` | Domain schemas (hukum kontrak) |
| `apps/analysis/src/seith_analysis/kronos_service.py` | Kronos inference wrapper |
| `apps/analysis/src/seith_analysis/run_analysis.py` | Pipeline analisis end-to-end |

---

## 6. ADR (Architecture Decision Records)

| ADR | Judul | Status |
|---|---|---|
| 0001 | Architecture Decisions (uv multi-env, vendoring, stack) | Accepted |
| 0002 | Contract & Ops Policies (wire contract, vendor pinning, auth) | Accepted |
| 0003 | Free Economic Calendar & Crypto News | Accepted |
| 0004 | License & Collaboration Policy | Accepted |

---

## 7. Keamanan & Operasional

| Area | Kebijakan |
|---|---|
| **Secrets** | Hanya di `.env` (gitignored). `.env.example` hanya placeholder. `.get_secret_value()` hanya di boundary konstruksi client. |
| **Money-path** | SignalActor → RiskManager → Exec. Approval gate manusia WAJIB (Tier-0). Trader tidak percaya wire `status`. |
| **Vendor** | Pin commit hash (Kronos `67b630e`, TradingAgents `a33fd4c`). Jangan `git pull` vendor tanpa prosedur upgrade eksplisit. |
| **9router** | Proses Tier-0 di `localhost:20128` — **DILARANG** dimatikan/direstart. |
| **Money-path approval** | `OrderProposal` status `APPROVED/SUBMITTED/FILLED` wajib `approved_by`. Approval gate manusia WAJIB untuk semua order. |
| **Trader verification** | Trader node **tidak percaya** field `status` dari wire — verifikasi ulang ke catatan sendiri. |

---

## 8. Referensi

- `docs/PRD.md` — Product Requirements Document
- `docs/adr/0001-architecture-decisions.md`
- `docs/adr/0002-contract-and-ops-policies.md`
- `docs/adr/0003-free-economic-calendar-and-crypto-news.md`
- `docs/adr/0004-license-and-collaboration-policy.md`
- `docs/GLOSSARY.md` — Glosarium istilah teknis (ID+EN)
- `docs/diagrams/architecture.mmd` — Diagram arsitektur (mermaid)
- `docs/diagrams/money_path.mmd` — Diagram money-path (mermaid)
- `docs/diagrams/deployment.mmd` — Diagram deployment (mermaid)
- `docs/kronos-notes.md` — Distilasi whitepaper Kronos
- `packages/seith-core/src/seith_core/schemas.py` — Domain schemas (hukum kontrak)
- `AGENTS.md` — Panduan wajib untuk agent harness

---

> **Catatan:** Dokumen ini adalah sumber kebenar tunggal arsitektur. Setiap perubahan arsitektur wajib update ADR dan dokumen ini.