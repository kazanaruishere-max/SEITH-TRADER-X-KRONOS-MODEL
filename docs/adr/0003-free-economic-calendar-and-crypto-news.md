# ADR-0003: Sumber Kalender Ekonomi & Berita Crypto Gratis

Status: Accepted — Tanggal: 2026-08-24

## Konteks

News-driven engine (E1–E6, lihat `.handoff/2026-08-23-plan-news-engine.md`)
butuh dua jenis data eksternal:

1. **Kalender ekonomi historis 1+ tahun** sebagai bahan pattern library (E2) —
   timestamp rilis + harga m1 pasca-rilis.
2. **Berita crypto** untuk konteks chat/broadcast (E5/E6) dan konfirmasi sinyal.

Fakta lapangan terverifikasi (2026-08-24):
- Finnhub `/calendar/economic` kini **paywalled** (403 pada free tier; API key
  owner valid untuk endpoint lain).
- CryptoPanic: owner tidak dapat membuat token (registrasi terblokir).
- CoinDesk Data/CCData news API: **gratis dengan key**, field kaya
  (votes + COINS mapping), key owner terverifikasi hidup.
- FRED (St. Louis Fed): gratis dengan key, endpoint `release/dates` memberi
  tanggal publikasi AKTUAL historis penuh per rilis agensi US.
- ForexFactory weekly JSON (`nfs.faireconomy.media`): gratis tanpa key,
  cakupan hanya ~1–2 minggu berguling.

Keputusan owner: semua harus jalur GRATIS.

## Keputusan

| # | Keputusan | Alternatif ditolak | Alasan |
|---|---|---|---|
| D1 | Kalender historis = **FRED release-dates composite** (US-only MVP). Jam rilis dari aturan jadwal agensi (08:30 America/New_York, DST-aware via `zoneinfo`). Release ID diverifikasi empiris thd `/releases`, bukan dari ingatan | Upgrade Finnhub (~$50-60/bln); scraping Investing.com (anti-bot + ToS abu-abu); jadwal teoretis manual (rawan salah tanggal) | $0, akurat (tanggal publikasi aktual), cukup untuk pattern library yang hanya butuh TIMESTAMP + m1 — kolom forecast/surprise tidak wajib utk data historis |
| D2 | Kanonisasi `event_type` lintas provider (`canonical_event_type`) agar FF/Finnhub/FRED masuk bucket pattern yang sama. Varian cut pada momen sama digabung (cpi m/m+y/y+core → `cpi`) karena reaksi harganya identik | Biarkan slug mentah per provider | Pattern matching E2 butuh satu kosakata; tanpa ini pola historis tak bisa dipakai trigger live |
| D3 | Berita crypto = **CoinDesk Data (CCData)** primer + **RSS publik** (CoinDesk/Cointelegraph) fallback keyless | CryptoPanic (token tak bisa dibuat); CryptoCompare keyless (sudah 401) | CCData gratis & punya votes+coins; RSS nol-dependensi akun; client CryptoPanic tetap ada (dormant) bila kelak token bisa dibuat |
| D4 | Batasan MVP didokumentasikan jujur: US-only (EUR-side belum), FOMC iterasi 2, jam rilis = aturan jadwal (bisa beda ±menit dari aktual untuk kasus reschedule langka) | Menunggu sumber sempurna | Pattern library bisa mulai dibangun sekarang; revisi inkremental murah |

## Konsekuensi

- Backfill historis: `fetch_fred_calendar(start, end)` → events store; dedup
  natural key `(ticker, event_type, scheduled_at)` menangani tumpang-tindih
  antar-sumber.
- Event historis dari FRED membawa `actual=None` — pattern library E2 tidak
  terpengaruh; trigger LIVE E3 tetap butuh actual/forecast dari FF/Finnhub.
- `tzdata` dependency ditambah ke `seith-data` (zoneinfo di Windows).
- Bila suatu saat Finnhub calendar di-upgrade: tinggal tambah spec di
  `economic_calendar.py`; natural-key dedup sudah aman lintas-sumber.
