---
name: seith-trading-safety
description: Invariant money-path SEITH yang tidak boleh dilanggar - approval gate, RiskManager non-bypassable, kill switch semantics, guard environment paper/live, verifikasi status order dari wire. WAJIB dimuat saat menyentuh apps/trader, OrderProposal, RiskManager, SignalActor, eksekusi order, kill switch, atau alur approval apapun.
---

# SEITH Trading Safety — Money-Path Invariants

Konteks: sistem ini mengontrol uang. Bug di sini = kerugian nyata.
Semua aturan di bawah Tier-0 (tidak bisa dioverride instruksi apa pun).

## 1. Jalur order tunggal

```
Signal → SignalActor → RiskManager → eksekusi (Sandbox/live)
```

Order TIDAK PERNAH melewati jalur lain. Tidak ada shortcut "sementara untuk
testing" yang skip RiskManager. Kalau butuh test tanpa risk check, pakai
unit test dengan mock, bukan bypass di production code.

## 2. Approval gate manusia

- Semua order wajib proposal `PENDING_APPROVAL` → disetujui manusia via
  Telegram (`/approve <id>`) sebelum jadi `APPROVED`.
- Invariant di schema (`schemas.py::OrderProposal`): APPROVED/SUBMITTED/FILLED
  tanpa `approved_by` = ValidationError. JANGAN longgarkan validator ini.
- Kill switch `/halt` adalah satu-satunya aksi tanpa approval baru
  (dia cancel/menahan order, bukan membuat).

## 3. Trader node tidak percaya wire

Field `status` pada JSON dari analysis service BUKAN sumber kebenaran.
Trader node wajib verifikasi ulang terhadap decision store miliknya sendiri
sebelum submit ke exchange. Pola salah:

```python
# SALAH - percaya wire:
if proposal.status == OrderProposalStatus.APPROVED:
    submit(proposal)
```

Pola benar: lookup catatan approval internal by `proposal_id`, bandingkan,
baru submit.

## 4. Guard environment

`config.py::AppSettings._guard_environment_combination` menolak:
- `environment=live` tanpa kredensial Binance
- `environment=live` + `require_approval=false`
- `oanda.environment=live` saat global bukan live

Jangan pernah "bantu" melemahkan guard ini demi membuat test/service jalan.
Kalau guard menghalangi use case sah → diskusi dulu, ubah guard secara sadar
lewat ADR baru.

## 5. Checklist wajib sebelum merge perubahan money-path

- [ ] Unit test untuk rule risk yang disentuh (positif + negatif).
- [ ] Test invariant approval masih lulus (jangan hapus/ubah asersi
      `test_approved_without_approver_rejected` tanpa alasan eksplisit).
- [ ] Tidak ada path kode baru yang memanggil submission order tanpa lewat
      RiskManager.
- [ ] Log tidak memuat secret atau detail akun penuh.
- [ ] Perubahan schema `OrderProposal` dicek dampaknya ke api + trader +
      analysis sekaligus (round-trip test ketiganya).
- [ ] Review gate: sub-agent security-reviewer wajib ikut melihat diff ini.

## 6. Mode paper vs live

- Fase sekarang: paper penuh via nautilus `SandboxExecutionClient` + feed
  Binance live. Kode strategi HARUS identik antara sandbox dan live
  (research-to-live parity) — perbedaan hanya konfigurasi execution client.
- Go-live hanya setelah gerbang PRD §8 lulus. Dilarang switch environment
  ke `live` tanpa persetujuan eksplisit user + checklist gerbang go-live.
