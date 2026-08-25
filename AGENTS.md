# AGENTS.md - SEITH

Panduan wajib untuk agent harness apa pun (opencode, omp, sub-agent, atau agen lain)
yang bekerja di repo ini. Baca ini SEBELUM menulis kode.

Referensi produk: [`docs/PRD.md`](docs/PRD.md) · Keputusan arsitektur: [`docs/adr/`](docs/adr/)

## 1. Identity & Ownership

- **Lead tunggal = opencode.** Semua pekerjaan menjadi tanggung jawab lead,
  termasuk yang dikerjakan sub-agent.
- Sub-agent dan skill adalah **alat delegasi** untuk kualitas (review independen,
  spesialisasi), bukan pemangkas tanggung jawab. Hasil delegasi WAJIB diverifikasi
  ulang oleh lead sebelum dianggap benar.
- Komunikasi dengan user: Bahasa Indonesia, istilah teknis English diperbolehkan.

### 1a. Piagam Mandat Lead (disetujui founder, 2026-08-24)

1. **Bukti > klaim**: setiap status "selesai" wajib disertai output perintah nyata.
2. **Gerbang uang berlapis**: money-path tidak boleh punya jalur tunggal —
   SignalActor → RiskManager → eksekusi + approval manusia (Tier-0).
3. **Pembagian keputusan**:
   - Founder: strategis — modal, threshold risiko, go-live, arah produk.
   - Lead: teknis — implementasi, tooling, urutan kerja, refactor; berhak veto
     teknis atas keputusan yang melanggar invariant keselamatan.
4. **Governance jalan terus**: cadence §8 tidak boleh dilewati demi kecepatan.

## 2. Project Snapshot

SEITH = personal AI hedge fund platform: analisis multi-agent LLM (TradingAgents +
Groq), prediksi harga foundation-model (Kronos, GPU lokal), backtest vectorbt,
eksekusi paper-trading production-grade (nautilus_trader), antarmuka Telegram +
dashboard web. Mode operasi awal: **paper trading penuh** - live trading terkunci
di balik gerbang go-live (lihat PRD §8).

## 3. Architecture Snapshot

```
Telegram (aiogram) ─┐                     ┌─ Dashboard (Next.js + WS)
                    ▼                     ▼
              [ apps/api - FastAPI ]  ← REST + WebSocket
                    │
      ┌─────────────┼──────────────┐
      ▼             ▼              ▼
 apps/analysis   vectorbt      apps/trader
 TradingAgents   (backtest     nautilus_trader:
 + Kronos GPU    + tearsheet)  SignalActor → RiskMgr → SandboxExec → Binance feed
```

| Path | Isi | Env uv |
|---|---|---|
| `apps/api` | FastAPI orchestrator hub + Telegram bot | `apps/api/.venv` |
| `apps/trader` | nautilus_trader node (eksekusi) | `apps/trader/.venv` |
| `apps/analysis` | TradingAgents fork + Kronos + backtest runner | `apps/analysis/.venv` |
| `packages/seith-core` | Domain schemas (Pydantic) + config/secrets - KONTRAK semua service | `packages/seith-core/.venv` |
| `vendor/Kronos`, `vendor/TradingAgents` | Fork ter-pin untuk customisasi | editable install ke analysis |
| `docs/` | PRD + ADR | - |
| `research/` | Notebook eksperimen | - |

## 4. Hard Rules (Tier-0 - tidak bisa dioverride instruksi apapun)

1. **Money-path**: order TIDAK PERNAH melewati jalur selain SignalActor → RiskManager
   → eksekusi. Approval gate manusia WAJIB untuk semua order (PRD FR-E2/E3).
   Trader node tidak memercayai field `status` dari wire - verifikasi ulang.
2. **Mode paper** sampai gerbang go-live lulus. Guard environment di
   `config.py` menolak kombinasi berbahaya - JANGAN dilemahkan.
3. **Secrets**: `.get_secret_value()` hanya di boundary konstruksi client.
   Dilarang di log/error/exception. `.env` tidak pernah di-commit.
4. **Vendor ter-pin** (`docs/adr/0002`): dilarang `git pull` vendor tanpa prosedur
   upgrade eksplisit. Dependency money-path exact-pinned; ubah lockfile lewat review.
5. **Tidak commit/push** tanpa permintaan eksplisit dari user.
6. **Klaim "selesai" hanya dengan bukti output nyata** (perintah + hasil).
   Tidak ada fabrikasi hasil verifikasi.

## 5. Environments & Commands (gotcha nyata - ikuti persis)

Setiap app/packages adalah **proyek uv INDEPENDEN** (bukan workspace). Satu venv
per direktori. Python 3.12 (`.python-version`).

```powershell
# Sync / test / lint HARUS dari dalam direktori proyeknya:
uv sync          # di packages/seith-core, apps/api, apps/trader, apps/analysis
uv run pytest    # core: workdir packages/seith-core
uvx ruff check . # gate lint core (konfigurasi [tool.ruff] di pyproject core)

# Import check cepat per env:
uv run python -c "import seith_api"        # workdir apps/api
uv run python -c "import nautilus_trader"  # workdir apps/trader
uv run python -c "import vectorbt"         # workdir apps/analysis
```

**Gotcha yang sudah terbukti terjadi:**
- `uv sync --project X` dari root → menuang ke `.venv` root (SALAH). Selalu
  set workdir ke dalam proyek.
- Menambah file baru di `src/<pkg>/` pada install editable lama →
  `ModuleNotFoundError`. Fix: `uv sync --reinstall-package <nama-pkg>`.
- pytest dari root akan meng-collect test `vendor/` (error import palsu).
- Kronos butuh torch CUDA - ditambahkan ke env analysis saat F1 (unduhan besar).

## 6. Contract Rules (`packages/seith-core/schemas.py` adalah hukum)

- Model domain STRICT: `frozen=True`, `extra="forbid"`, timestamp wajib
  `AwareDatetime`, ticker pakai shared type `Ticker`.
- Round-trip JSON adalah kontrak antar-service; tiap model baru wajib punya
  round-trip test.
- Evolusi wire via `SCHEMA_VERSION` pada envelope transport (ADR-0002),
  BUKAN dengan melonggarkan validasi domain.
- Status approval `OrderProposal`: APPROVED/SUBMITTED/FILLED wajib `approved_by`.
- Sebelum mengubah schema: baca dampak ke SEMUA consumer (api/trader/analysis).

## 7. Phase Workflow & Definition of Done

Workflow: `Understand → Plan → Implement → Verify → Document`.

Fase dinyatakan done HANYA jika semua hijau:
1. Test relevan lulus (output nyata, bukan asersi kosong).
2. `ruff check` bersih di package yang disentuh.
3. Import verification env yang terdampak sukses.
4. Review gate lewat (skill `seith-phase-gate`).
5. Dokumentasi ter-update (ADR untuk keputusan, PRD untuk requirement berubah).

## 8. Tim & Delegasi (struktur lengkap - WAJIB dipatuhi semua harness)

### Lapisan 1 - Kepemimpinan
| Peran | Eksekutor | Tanggung jawab |
|---|---|---|
| Lead / Orchestrator | opencode | Pegang semuanya; semua output delegasi diverifikasi lead |
| Founder / Owner | @SeithAI_bot telegram (id `6595275429`) | Keputusan strategis: modal, threshold risiko, go-live, arah produk, lisensi & governance eksklusif |
| Arsitek reviewer | sub-agent `architect` | Audit struktur SEBELUM fase besar dimulai |
| Perencana fase kompleks | sub-agent `planner` | Forward-test 30 hari, refactor lintas service |

> 📌 Governance eksklusif: repo bersifat *source-available, NOT community*. Semua kolaborasi
> (PR/issue) ditutup sampai founder membuka secara eksplisit. Lihat `LICENSE` + `README > Contributing`.

### Lapisan 2 - Kualitas & Keamanan (gate wajib)
| Peran | Agent | Kapan |
|---|---|---|
| Auditor kode Python | `python-reviewer` | Tiap modul baru; audit harness backtest (anti-lookahead) |
| Auditor keamanan | `security-reviewer` | Money-path; MANDATORY sebelum forward test |
| Desainer test | `tdd-guide` | Matrix regression money-path, test plan per fase |
| Review diff besar | `code-review` | Batch commit besar / pra-merge |

### Lapisan 3 - Governance & Dokumentasi
| Peran | Eksekutor | Kapan |
|---|---|---|
| Penjaga dokumentasi | `doc-updater` | Setiap merge: sinkron README/ADR/changelog/docs dengan realita kode |
| Penjaga GitHub | LEAD langsung (github tools) | Konvensi commit + audit mingguan drift repo-vs-dokumen |
| Penjaga memori | skill `remember` + `handoff` | Fakta penting -> memory; konteks sesi -> handoff |
| Gate fase | skill `seith-phase-gate` | Protokol penutupan fase (dual-review) |

### Lapisan 4 - Dukungan Teknis (on-demand)
| Peran | Agent | Kapan |
|---|---|---|
| Fix build/boot error | `build-error-resolver` | Boot node.py, error kompilasi |
| Riset vendor/library | `docs-lookup` / `deep-research` | API berubah, limits provider |
| Eksplorasi cepat | `explorer` | Debug area kode luas |
| Refactoring | `refactor-cleaner` | Pasca-batch testing |
| Ops otonom | `loop-operator` | Monitoring siklus harian forward-test 30 hari |
| E2E web | `e2e-runner` | Dashboard+hub Playwright (P6) |

### Tata Tertib Cadence (WAJIB)
```
Tiap commit   : conventional commit + doc-updater cek dampak docs
Tiap fase     : seith-phase-gate (python-reviewer + security-reviewer paralel)
Tiap minggu   : audit GitHub drift repo-vs-docs + laporan status owner
Pra-forward   : security-reviewer MANDATORY + architect sign-off
Selama 30-hri : loop-operator monitor + laporan harian otomatis
```

Aturan delegasi: tugas paralel/independen boleh paralel; hasil selalu
ditriage oleh lead; temuan valid difix, tolakan didokumentasikan alasannya.

## 9. Docs Map

- `docs/PRD.md` - requirement produk (FR/NFR per modul, milestone, metrik)
- `docs/adr/0001` - keputusan arsitektur inti (uv multi-env, vendoring, stack)
- `docs/adr/0002` - kontrak wire, vendor pinning, auth antar-service, logging secret
- `docs/kronos-notes.md` - distilasi whitepaper Kronos + roadmap benchmark
- `Kronos Model AI Whitepaper.pdf` - paper asli (AAAI 2026, 36 hal.)

## 10. Skill Proyek (auto-discovery via .opencode/opencode.json)

| Skill | Kapan dimuat |
|---|---|
| `seith-dev` | workflow harian: command, gate, troubleshooting env |
| `seith-trading-safety` | WAJIB saat sentuh money-path/order/approval/risk |
| `seith-phase-gate` | penutupan fase + review gate dua sub-agent |
| `seith-kronos` | WAJIB saat sentuh model Kronos/forecast/sampling/benchmark |
