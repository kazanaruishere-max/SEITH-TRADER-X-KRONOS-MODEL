# Kronos — Catatan Distilasi Whitepaper (AAAI 2026)

Sumber: `Kronos Model AI Whitepaper.pdf` (36 hal.) - Shi et al., Tsinghua.
Tujuan dokumen: satu-satunya rujukan ringkas agar agent/human memahami MODEL
yang dipakai sistem ini secara benar sebelum menyentuh kode forecast.

## 1. Apa itu Kronos

Foundation model KHUSUS untuk bahasa pasar keuangan = deret candlestick (K-line).
Berbeda dari TSFM umum: dirancang untuk karakteristik high-noise data finansial.

**Arsitektur dua tahap:**
1. **Tokenizer spesial**: mendiskritisasi OHLCV kontinu menjadi **token hierarkis**
   - mempertahankan dinamika harga SEKALIGUS pola aktivitas trading (volume).
2. **Decoder autoregresif**: pre-training pada korpus masif → belajar representasi
   temporal & lintas-aset.

## 2. Skala & Model Terbuka

| Model | Parameter | Context | Status |
|---|---|---|---|
| Kronos-mini | 4.1M | 2048 | terbuka |
| Kronos-small | 24.7M | 512 | terbuka |
| **Kronos-base** | **102.3M** | **512** | **terbuka - dipakai SEITH** |
| Kronos-large | 499.2M | 512 | tertutup |

Pre-training corpus: **12+ miliar record K-line dari 45 exchange global**.

## 3. Angka Benchmark Zero-shot (klaim paper)

| Tugas | Keunggulan Kronos |
|---|---|
| Prediksi harga - RankIC | **+93%** vs TSFM terdekat · **+87%** vs baseline non-pre-trained terbaik |
| Volatilitas - MAE | −9% |
| Generasi K-line - fidelity | +22% |

Lima tugas evaluasi: prediksi harga & return (IC/RankIC), volatilitas (MAE/R²),
generasi K-line (Disc. Score/IC/RankIC), simulasi investasi (**AER/IR**) -
di simulasi investasi, base & large mengungkuli seluruh kategori baseline.

## 4. Guidance Sampling Resmi (paling operasional!)

> Temperature scaling + top-p (nucleus) sampling. Untuk tugas presisi tinggi,
> akurasi ditingkatkan dengan **menghasilkan beberapa trajektori masa depan
> (Monte Carlo rollouts) dan merata-ratakan nilai dekode hasilnya** -
> pendekatan ini "consistently improves forecast quality".

Implikasi wajib untuk SEITH:
- `sample_count` > 1 BUKAN opsional - itu mekanisme akurasi inti paper.
- **Confidence yang benar bersumber dari distribusi antar-path**, bukan heuristik
  eksternal (lihat roadmap §6).

## 5. Parameter Operasional SEITH (terverifikasi berjalan)

- Model: `NeoQuasar/Kronos-base` + `NeoQuasar/Kronos-Tokenizer-base` (HF Hub,
  ter-cache lokal ~450MB), device CUDA (RTX 4050, ±0.5-1GB VRAM saat inference)
- Input: OHLCV DataFrame UTC tz-aware; lookback default 400 bar (BATAS KERAS context=512)
- Sampling default kita: `T=1.0`, `top_p=0.9`, `sample_count=8`
- Output: forecast OHLCV per horizon bar → diserialisasi `ForecastResult` (seith-core)
- Kode inference: `apps/analysis/src/seith_analysis/kronos_service.py`

## 6. Roadmap Penguatan Benchmark (disepakati owner)

1. **Confidence Monte Carlo empiris**: ganti heuristik vol-ratio dengan fraksi
   path searah mean dari N rollout (sumber confidence versi paper).
2. **Evaluation harness lokal**: hitung IC/RankIC expected_return vs realized
   rolling window per ticker, dibanding baseline naiv persistence - klaim
   "peak" harus terukur di data sendiri.
3. **Gate metrik**: signal Kronos hanya dipercaya penuh bila RankIC lokal > 0
   sustain (masuk PRD metrics).
4. **Fine-tune per-market** (vendor `finetune/`, contoh Qlib-based): SETELAH
   baseline paper 30 hari - sesuai PRD Open Questions.

## 7. Kesalahan Umum yang Harus Dihindari Agent

- Jangan panggil Kronos di env api/trader (torch hanya ada di env analysis).
- Jangan set lookback > 512 (context keras model) atau < ~200 (informasi kurang).
- Jangan pakai `sample_count=1` demi cepat tanpa alasan terdokumentasi -
  melanggar guidance resmi paper §4.
- Jangan percaya satu angka forecast tanpa lihat dispersi antar-path.
