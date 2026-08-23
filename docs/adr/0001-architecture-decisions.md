# ADR-0001: Keputusan Arsitektur Inti SEITH

Status: Accepted · Tanggal: 2026-08-23

## Konteks

Platform AI hedge fund personal dengan komponen berat yang punya kebutuhan dependency
bertabrakan: PyTorch CUDA (Kronos), Rust-native wheel (nautilus_trader), Numba/NumPy
(vectorbt), dan LangGraph stack (TradingAgents).

## Keputusan

| # | Keputusan | Alternatif ditolak | Alasan |
|---|---|---|---|
| D1 | Monorepo multi-env via **proyek uv independen** (bukan uv workspace), Python 3.12, satu venv per app; ketergantungan antar-package via editable path dependency | uv workspace; conda; satu env besar | uv modern membuat SEMUA workspace member berbagi satu `.venv` di root dan swap dependency saat sync member lain - melanggar isolasi torch/Kronos vs nautilus vs LangGraph. Proyek independen = venv per app tanpa ceremony tambahan. Trade-off: lockfile per-app, drift versi antar-service diterima karena justru ingin terisolasi |
| D2 | Kronos & TradingAgents di-vendor (`vendor/`) sebagai fork editable | Install pip murni | Wajib customisasi: analyst crypto/forex, wiring Groq, integrasi forecast |
| D3 | nautilus_trader & vectorbt sebagai dependency pip biasa | Fork | Engine produksi/API stabil; ikuti upstream, hindari maintenance fork besar |
| D4 | LLM = Groq (`llama-3.3-70b-versatile` default, configurable) | OpenAI/Claude/Ollama | Murah-cepat, support native di TradingAgents v0.3+, retry budget mengatasi rate limit |
| D5 | Paper trading = nautilus `SandboxExecutionClient` + live Binance data feed | Simulasi sendiri; backtest-only | Parity kode penuh dengan live execution path saat go-live |
| D6 | Komunikasi antar-service: HTTP internal + event stream (fill/posisi/PnL) fan-out via WebSocket core API | Message broker eksternal (Redis/NATS) dari awal | MVP simpel; Redis opsional menyusul kalau skala butuh |
| D7 | Human-in-the-loop: order proposal default wajib approve via Telegram | Full-auto sejak awal | Kontrol risiko fase awal; full-auto jadi config flag setelah trust terbangun |
| D8 | Storage: Parquet (OHLCV historis) + SQLite (metadata/decisions/orders) | Postgres dari awal | Zero-ops lokal Windows; migrasi Postgres trivial karena akses lewat repository layer |
| D9 | Dashboard: Next.js + TypeScript + lightweight-charts | Streamlit/Gradio | Realtime WebSocket, kualitas UI profesional, siap deploy VPS |
| D10 | Telegram framework: aiogram 3.x (async) | python-telegram-bot | Async-native cocok dengan FastAPI event loop |

## Konsekuensi

- Setiap app sync independen: `uv sync --project apps/<name>`.
- Vendor fork perlu disinkip manual dengan upstream secara berkala.
- Torch CUDA ditambahkan ke `apps/analysis` di Fase 1 (unduhan besar, tidak dibutuhkan P0).
