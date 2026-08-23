---
name: seith-kronos
description: Pengetahuan mendalam model Kronos (foundation model K-line, AAAI 2026) + parameter operasional SEITH. WAJIB dimuat saat menyentuh kronos_service, forecast inference, parameter sampling (T/top_p/sample_count), evaluation RankIC/IC, fine-tune Kronos, atau apapun yang berhubungan dengan model prediksi harga GPU.
---

# Kronos di SEITH — Knowledge Skill

Rujukan lengkap: `docs/kronos-notes.md` (distilasi whitepaper) ·
Whitepaper asli: `Kronos Model AI Whitepaper.pdf` (36 hal., AAAI 2026).

## Fakta inti (jangan sampai salah)

- Kronos = tokenizer OHLCV → token hierarkis → decoder autoregresif.
  Pre-trained **12B record K-line, 45 exchange**.
- Yang kita pakai: **Kronos-base (102.3M, context keras 512)** via HF Hub,
  device CUDA lokal. Inference ±3 detik / 24-bar horizon di RTX 4050.
- Benchmark zero-shot paper: RankIC +93% vs TSFM terdekat; vol MAE −9%;
  generasi +22%; simulasi investasi mengungkuli semua kategori baseline.

## Aturan operasional (Tier-1)

1. **sample_count > 1 wajib** — paper: MC rollouts di-rata-rata
   "consistently improves forecast quality". Default SEITH: 8.
2. **lookback ≤ 512** (context keras). Default 400.
3. Kronos HANYA jalan di env `apps/analysis` (torch CUDA). Jangan import
   kronos_service dari env lain.
4. **Confidence versi paper = distribusi antar-path** (fraksi path searah
   mean). Heuristik vol-ratio yang sekarang ada adalah placeholder fase awal —
   upgrade ke MC empiris sudah disepakati (docs/kronos-notes.md §6).
5. Klaim kualitas WAJIB lewat evaluation harness lokal (RankIC vs persistence),
   bukan dari angka paper — zero-shot di market lain harus dibuktikan ulang.

## Parameter runtime terverifikasi

```
model:    NeoQuasar/Kronos-base        tokenizer: Kronos-Tokenizer-base
device:   cuda (fallback auto)         T=1.0  top_p=0.9  sample_count=8
input:    OHLCV Parquet UTC tz-aware   lookback=400
output:   ForecastResult (seith-core)  + parquet path di data dir
```

## Fine-tune (jangan lakukan sebelum disepakati)

Script vendor ada di `vendor/Kronos/finetune/` (contoh pipeline Qlib, A-share).
Kebijakan: fine-tune per-market SETELAH baseline paper 30 hari + RankIC lokal
terukur (PRD Open Questions). Jangan jalankan tanpa approval owner.
