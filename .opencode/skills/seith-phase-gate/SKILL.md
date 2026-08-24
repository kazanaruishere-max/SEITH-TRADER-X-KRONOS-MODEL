---
name: seith-phase-gate
description: Protokol penutupan fase SEITH - review gate dua sub-agent paralel, triage temuan, dokumentasi tolakan, laporan verifikasi aktual. Gunakan saat sebuah fase (P0-P6) dinyatakan selesai dan sebelum lanjut ke fase berikutnya, atau saat user minta "review gate", "tutup fase", atau "verifikasi akhir".
---

# SEITH Phase Gate — Protokol Penutupan Fase

Fase dinyatakan done HANYA jika semua langkah di bawah hijau. Tidak ada
pengecualian "karena kecil".

## Langkah 1 — Verification gate teknis (lead jalankan sendiri)

Jalankan dan simpan output nyata:

1. `uv run pytest -q` di setiap package yang disentuh fase ini.
2. `uvx ruff check .` pada package tersebut.
3. Import verification tiap env terdampak (`seith-dev` cheat sheet).
4. Smoke test spesifik fase (mis. P1: satu forecast Kronos end-to-end).

## Langkah 2 — Review gate dua sub-agent paralel (read-only)

Panggil DUA sub-agent sekaligus dalam satu pesan:

- `python-reviewer` — fokus kualitas: desain, idiom Pydantic/async, test gap,
  maintainability, performa.
- `security-reviewer` — fokus keamanan: secret, input validation, money-path
  bypass, dependency surface.

Konteks yang wajib diberikan ke reviewer:
- Daftar file yang berubah sejak review terakhir.
- Konteks arsitektur singkat (mereka belum lihat sesi ini).
- Instruksi eksplisit READ-ONLY + format temuan
  `[BLOCKER]/[CRITICAL]/[HIGH]/[MAJOR]/[MINOR]/[NIT]`.

## Langkah 3 — Triage

Untuk setiap temuan, putuskan salah satu:

| Keputusan | Aksi |
|---|---|
| Valid & murah sekarang | Fix langsung, masuk verification gate ulang |
| Valid tapi fase nanti | Masuk todo fase terkait + catat di ADR jika keputusan arsitektur |
| False positive / trade-off sadar | Dokumentasikan alasan di ADR fase terkait — jangan diam-diam diabaikan |

Aturan: temuan CRITICAL/BLOCKER pada money-path atau secret WAJIB fix sebelum
fase ditutup. Tidak ada "nanti dulu" untuk kategori itu.

## Langkah 4 — Dokumentasi & Governance

- Keputusan arsitektur baru → ADR baru di `docs/adr/` (nomor urut).
- Requirement berubah → update `docs/PRD.md`.
- Pelajaran operasional gotcha baru → update `AGENTS.md` §5 atau skill `seith-dev`.
- **doc-updater pass**: sinkronkan README/ADR/changelog dengan realita kode
  yang berubah di fase ini (WAJIB - lihat AGENTS.md §8 Lapisan 3).

## Cadence Governance Terkait Fase (AGENTS.md §8)

- [ ] Conventional commit + dampak docs dicek tiap commit dalam fase.
- [ ] Audit drift GitHub repo-vs-dokumen bila fase melewati >1 minggu.
- [ ] Bila fase ini gerbang pra-forward-test: security-reviewer MANDATORY
      + architect sign-off tercatat sebelum forward test boleh dimulai.

## Langkah 5 — Laporan penutupan fase ke user

Format wajib:

```
# Fase X — SELESAI / BLOKIR

## Verifikasi (perintah + hasil nyata)
| Gate | Perintah | Hasil |
...

## Temuan review & statusnya
- [fixed] ...
- [dijadwalkan fase Y] ...
- [ditolak, alasan] ...

## Yang berubah sejak fase lalu
...

## Langkah fase berikutnya
...
```

## Anti-pattern yang dilarang

- Klaim "review sudah jalan" tanpa menempelkan hasil nyata sub-agent.
- Menutup fase dengan temuan CRITICAL belum ditriage.
- Menambah todo tanpa mengubah status todo lama yang relevan.
- Melewati Langkah 1 karena "kan sudah dites pas ngerjain".
