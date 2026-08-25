# ADR-0004 — License & Collaboration Policy

- **Status**: Accepted (Founder, 2026-08-25)
- **Pilar**: Governance / Open Source
- **Konteks**: Founder menginginkan "open source" (agak) untuk SEITH agar orang
  dapat **membaca, menjalankan, menyalin** source, **tapi tidak ada kolaborasi,
  fork-management, atau campur tangan pihak ketiga** sampai founder membuka.
  Ini menegah tegah dengan definisi OSI-standard open source (yang menuntut hak
  modifikasi & redistribution bebas serta tidak melarang kolaborasi).

## Opsi yang Dipertimbangkan

| # | Opsi | OSI? | Collaboration | Owner-control | Kelayakan |
|---|---|---|---|---|---|
| 1 | **Custom `SEITH Limited Source License v1.0`** | ✗ | Ditutup di dalam license text | Eksklusif | **Ditolak**: lisensi custom non-OSI berisiko hukum tidak jelas, dan menyatu kelongkrakkan license + policy (gagal prinsip pemisahan) |
| 2 | **GPL-3.0 + collaboration-closed repo policy** | ✓ (OSI) | Ditutup via kebijakan di atas (bukan license) | Eksklusif | **Dipilih**: license standar yang teruji, kebijakan kolaborasi dipisah ke README + Terms-of-Use |
| 3 | Proprietary / "all rights reserved" | ✗ | Ditutup | Eksklusif | Ditolak: kehilangan benefit "read/use gratis" yang founder mahu |

## Keputusan

**Opsi 2**: GPL-3.0-or-later sebagai license, dengan **kebijakan kolaborasi terbuka tertutup**
sebagai Terms-of-Use di README (`Contributing — CLOSED`).

## Asumsi

- Dua lapisan hak: (a) *license rights* (read/run/copy/mod-fork secara teknis —
  dilindungi GPL strong copyleft) dan (b) *governance rights* (kolaborasi, fork-
  management, interference — **disisi pihak ketiga, dikontrol founder via kebijakan**).
- GPL-3.0 strong copyleft sudah mencegah fork.propenset menjadi proprietary;
  founder tetap bisa menolak PR/issue (GPL tidak memaksa maintainer menerima kontribusi).
- Jika founder kelak menginginkan **True OSI-community open source** (kolaborasi terbuka),
  cukup ganti README contribution policy → license tetap GPL-3.0.

## Implikasi

- Repo bisa dipelajari/dev/di-run oleh siapa saja secara legal.
- GitHub tidak akan menampilkan "collaboration welcome" otomatis karena kebijatan ditutup.
- Jika contributor luar (dengan izin) submit patch, contributor harus setuju kebijakan
  kolaborasi ini (invite-only) — rekam di AUTHORS/LICENSE-GRANT.

## Catatan Hukum

Ini bukan nasihat hukum. Untuk keperluan komersial/derivation, konsultasikan counsel.
