# PRD - SEITH: Personal AI Hedge Fund Platform

Versi: 0.1 · Tanggal: 2026-08-23 · Status: Approved

## 1. Ringkasan Eksekutif

SEITH adalah platform trading personal yang mereplikasi workflow hedge fund: tim agen
LLM menganalisis pasar (crypto, saham US, forex), foundation model memprediksi harga,
backtest memvalidasi strategi, dan engine eksekusi event-driven production-grade
menjalankan paper trading dengan data market live. Kontrol penuh via chat Telegram dan
dashboard web realtime.

## 2. Masalah

- Analisis manual tersebar di banyak tools, tidak sistematis, tanpa jejak keputusan (audit trail).
- Tools retail terfragmentasi: charting, backtest, dan eksekusi saling lepas.
- Keputusan trading butuh konsistensi proses: data -> analisis -> debat -> keputusan -> validasi -> eksekusi -> review.

## 3. Tujuan & Non-Tujuan

### Tujuan v1

1. Analisis multi-agent multi-market end-to-end (crypto Binance, saham US, forex majors).
2. Prediksi harga Kronos (GPU lokal) terintegrasi ke pipeline analisis.
3. Validasi strategi via vectorbt sebelum signal boleh dieksekusi.
4. Paper trading via nautilus_trader `SandboxExecutionClient` + live Binance feed.
5. Risk management hard-gated: sizing, max drawdown breaker, daily loss cap, kill switch.
6. Antarmuka Telegram (chat + approval + notifikasi) dan dashboard web realtime.

### Non-tujuan v1

- Live trading uang riil (gerbang: 30 hari paper terbukti stabil).
- Multi-user / SaaS.
- HFT / latency-sensitive strategies.
- Portfolio optimization institusional penuh (factor neutralization).

## 4. Persona

**Owner (trader tunggal):** kontrol penuh sistem via Telegram & dashboard, approve/reject
order proposal, pantau risiko realtime.

## 5. Persyaratan Fungsional

### 5.1 Telegram Bot (`apps/api`)

| ID | Requirement |
|---|---|
| FR-T1 | `/analyze <ticker>` -> pipeline analisis penuh, kirim report terstruktur |
| FR-T2 | `/portfolio`, `/positions`, `/pnl` -> status akun realtime |
| FR-T3 | `/approve <id>` / `/reject <id>` -> human-in-the-loop order approval |
| FR-T4 | `/halt` -> kill switch global: cancel order pending + halt semua strategy |
| FR-T5 | Notifikasi otomatis: fill, risk breach, daily summary |
| FR-T6 | `/report` -> tearsheet PDF harian/mingguan |

### 5.2 Dashboard Web (`apps/dashboard`)

| ID | Requirement |
|---|---|
| FR-D1 | Equity curve realtime + benchmark |
| FR-D2 | Tabel posisi open/closed + PnL |
| FR-D3 | Candlestick chart + overlay forecast Kronos (confidence band) |
| FR-D4 | Timeline keputusan agent (analyst reports, debate log, reasoning) |
| FR-D5 | Risk panel: exposure, drawdown, status limits |
| FR-D6 | Riwayat backtest + tearsheet |

### 5.3 Analysis Service (`apps/analysis`)

| ID | Requirement |
|---|---|
| FR-A1 | Pipeline TradingAgents (analyst -> debate -> trader -> risk -> PM) via Groq |
| FR-A2 | Kronos forecast service: OHLCV -> forecast + confidence band (< 5 detik) |
| FR-A3 | Custom analyst crypto (CoinGecko, Fear&Greed, Binance) & forex (OANDA) |
| FR-A4 | Decision JSON terstandar (Pydantic) + persist decision log audit trail |
| FR-A5 | LLM caching + retry budget untuk Groq rate limit |

### 5.4 Research/Backtest (`research/` + `apps/analysis`)

| ID | Requirement |
|---|---|
| FR-R1 | Validasi signal via vectorbt dengan model fees & slippage realistis |
| FR-R2 | Parameter sweep + walk-forward analysis |
| FR-R3 | Tearsheet otomatis tersimpan, tampil di dashboard |

### 5.5 Trader Node (`apps/trader`)

| ID | Requirement |
|---|---|
| FR-E1 | nautilus live node + Binance market data adapter |
| FR-E2 | SignalActor: konsumsi signal -> order dengan **approval gate WAJIB** untuk semua order (kill switch adalah satu-satunya jalur tanpa approval baru; trader node wajib verifikasi ulang approval terhadap catatan miliknya sendiri, tidak percaya field `status` dari wire) |
| FR-E3 | RiskManager: position sizing, max position, max daily loss, max-DD circuit breaker - 100% order wajib lewat sini |
| FR-E4 | SandboxExecutionClient (paper mode); kode parity dengan mode live |
| FR-E5 | Event stream (fill, posisi, PnL) -> core API -> WebSocket fan-out |

### 5.6 Data Layer (`packages/seith-data`)

| ID | Requirement |
|---|---|
| FR-DT1 | Ingestion: Binance (ccxt/ws), yfinance (saham US), OANDA v20 (forex) |
| FR-DT2 | Storage: Parquet (historis) + SQLite (metadata, decisions, orders) |
| FR-DT3 | Backfill historis + incremental update terjadwal |
| FR-DT4 | Data quality checks: gap, duplikat, outlier |

## 6. Persyaratan Non-Fungsional

- **NFR-Security:** secrets via `.env`/keyring, tidak pernah di log/kode; saat go-live: key Binance trade-only tanpa withdrawal + IP whitelist. Komunikasi antar-service bind `127.0.0.1` di fase lokal; sebelum VPS/Docker lintas host: shared-token header wajib untuk semua endpoint write (signal/approve/halt) + token pada WebSocket handshake. Kebijakan logging: `.get_secret_value()` hanya boleh dipanggil di boundary konstruksi client - dilarang masuk log/error message. Rate limiting: cap harian biaya Groq + debounce command Telegram `/analyze`.
- **NFR-Reliability:** restart-safe (nautilus cache persistence), graceful shutdown, checkpoint resume TradingAgents.
- **NFR-Observability:** structured logging, decision log sebagai audit trail lengkap.
- **NFR-Performance:** Kronos forecast < 5 dtk; analisis penuh < 3 menit; dashboard latency < 500ms.
- **NFR-Portability:** Windows lokal dulu, siap migrasi Docker/VPS tanpa rewrite.
- **NFR-Testability:** critical path (signal->order, risk rules, kill switch) wajib unit + integration tests.

## 7. Arsitektur & Stack

```
Telegram (aiogram) --+                    +- Dashboard (Next.js + WS)
                     v                    v
              [ Core API - FastAPI ]  <- REST + WebSocket
                    |
      +-------------+---------------+
      v             v               v
 Analysis Svc   vectorbt       Trader Node
 TradingAgents  (backtest      nautilus_trader:
 + Kronos GPU   + tearsheet)   SignalActor -> RiskMgr -> SandboxExec -> Binance feed
```

| Layer | Teknologi |
|---|---|
| Orchestration | FastAPI + aiogram 3.x + WebSocket |
| Brain | TradingAgents (vendored fork) + Groq LLM |
| Forecast | Kronos-base (vendored) + PyTorch CUDA lokal |
| Backtest | vectorbt + pandas/pyarrow |
| Execution | nautilus_trader (pip, stable API) |
| Data | ccxt, yfinance, OANDA v20 -> Parquet + SQLite |
| UI | Next.js + TypeScript + lightweight-charts |
| Env mgmt | uv workspace, Python 3.12 per-app env |

## 8. Metrik Keberhasilan (fase paper, 30 hari)

1. Uptime pipeline analisis > 99% selama jam operasional.
2. 100% order melewati RiskManager (zero bypass).
3. >= 20 analisis lengkap dengan decision log terdokumentasi.
4. Deviasi backtest-vs-paper terukur dan ter-log per trade.
5. Max drawdown paper account < batas konfigurasi (default 10%).
6. Kill switch `/halt` berfungsi < 2 detik dari command.

## 9. Milestone

| Fase | Deliverable | Estimasi |
|---|---|---|
| F0 Fondasi | Monorepo, uv workspace, schemas, vendor clone | 1-2 hari |
| F1 Data+Kronos | Ingestion multi-source, storage, Kronos GPU service | 3-5 hari |
| F2 Otak Analisis | TradingAgents+Groq custom, integrasi Kronos, decision JSON | 5-7 hari |
| F3 Backtest | vectorbt pipeline validasi signal + tearsheet | 3-4 hari |
| F4 Eksekusi Paper | nautilus node, SignalActor, RiskManager, SandboxExec | 5-7 hari |
| F5 Interface | Telegram bot penuh + dashboard Next.js realtime | 7-10 hari |
| F6 Hardening | Logging, alerting, rekonsiliasi, E2E smoke test | ongoing |

## 10. Risiko & Mitigasi

| Risiko | Mitigasi |
|---|---|
| Dependency conflict torch<->nautilus<->vectorbt | Multi-env uv + isolasi proses (didisain dari awal) |
| Groq rate limit saat debate multi-agent | Model kecil utk quick-agents, cache, retry budget |
| Kronos zero-shot lemah di saham/forex | Gate F3: backtest wajib lulus sebelum eksekusi |
| Windows quirks (TA-Lib dll.) | pandas-ta; fallback Docker Desktop |
| Kebocoran API key | Paper phase tanpa key trade; go-live: least-privilege + IP whitelist |

## 11. Open Questions

1. Kriteria go-live ke modal riil? (usulan: paper 30 hari + Sharpe > 1 + DD terkendali)
2. Fine-tune Kronos per-market - setelah baseline 30 hari?
3. Data saham intraday US lebih baik (Polygon.io berbayar)? - tunda sampai dibutuhkan.

## 12. Referensi Upstream

- https://github.com/shiyu-coder/Kronos - foundation model prediksi K-line
- https://github.com/TauricResearch/TradingAgents - multi-agent LLM trading framework
- https://github.com/nautechsystems/nautilus_trader - execution engine
- https://github.com/polakowo/vectorbt - backtesting & visualisasi vektorisasi
- https://github.com/wilsonfreitas/awesome-quant - referensi riset quant
- https://github.com/grananqvist/Awesome-Quant-Machine-Learning-Trading - referensi riset ML
