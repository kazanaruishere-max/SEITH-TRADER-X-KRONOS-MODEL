# ADR-0002: Kebijakan Kontrak Wire, Vendor Pinning, Auth, Logging, Rate Limit

Status: Accepted · Tanggal: 2026-08-23 · Trigger: hasil review gate P0 (security + python reviewer)

## Konteks

Review gate P0 menemukan risiko desain kontrak antar-service (bypass approval,
version-skew wire, silent-unconfigured config) dan kebijakan operasional yang
belum tertulis. Semua keputusan di bawah lahir dari temuan tersebut.

## 1. Evolusi Kontrak Wire

- Domain models di `seith_core/schemas.py` tetap STRICT (`extra="forbid"`,
  aware-UTC, ticker ternormalisasi). Tidak pernah dilonggarkan demi kompatibilitas.
- Evolusi versi ditangani `SCHEMA_VERSION` (konstanta di `seith_core`) pada
  **envelope transport** `{schema_version, payload}` di layer komunikasi
  (diimplementasikan saat F4/F5 ketika service benar-benar berkomunikasi via HTTP).
- Parsing inbound di transport layer toleran (abaikan field asing setelah
  versi cocok); domain layer tetap menolak.
- Invariant approval `OrderProposal`: status APPROVED/SUBMITTED/FILLED wajib
  `approved_by` (ditegakkan validator model). Transisi antar-status diverifikasi
  service (decision store), dan trader node WAJIB verifikasi ulang approval
  terhadap catatan miliknya - field `status` dari wire tidak dipercaya (PRD FR-E2).

## 2. Vendor Pinning

- `vendor/Kronos` dan `vendor/TradingAgents` di-**detach** dari branch upstream
  (checkout --detach) ke commit snapshot. Upgrade = prosedur manual:
  `git fetch` -> review diff -> commit pin baru -> catat hash di ADR ini.
- Vendor di-track sebagai **plain source** (inner `.git` di-strip) agar repo
  self-contained; hash pin di bawah adalah satu-satunya referensi upstream.
  Upgrade = ganti direktori dari clone baru pada commit ter-pin + re-apply
  patch customisasi kita.
- Dependency money-path di-pin exact: `nautilus_trader==1.231.0`,
  `vectorbt==1.1.0`. Perubahan lockfile wajib lewat review.

| Vendor | Commit pin | Tanggal pin |
|---|---|---|
| Kronos | `67b630e67f6a18c9e9be918d9b4337c960db1e9a` | 2026-08-23 |
| TradingAgents | `a33fd4c0f134485a43553a2c23a63cb14adbd88f` | 2026-08-23 |

## 3. Auth Antar-Service

- Fase lokal (F0-F5): semua service bind `127.0.0.1` saja.
- Sebelum VPS/Docker lintas host: shared-token statik pada header semua
  endpoint write (submit signal, approve, halt) + token pada WebSocket
  handshake. Retrofit auth ke protokol yang sudah jalan lebih mahal daripada
  merancangnya sekarang - keputusan ini dikunci sekarang, implementasi F4/F5.

## 4. Kebijakan Logging Secret

- `SecretStr.get_secret_value()` HANYA boleh dipanggil di boundary konstruksi
  client (mis. saat membuat instance client Binance/Groq/OANDA).
- Dilarang keras: secret masuk log, error message, exception text, atau
  struktur data yang di-serialize.
- Tes anti-leak (`secret_not_leaked_in_repr`) adalah pola wajib untuk setiap
  secret baru.

## 5. Rate Limiting & Biaya LLM

- Cap harian biaya Groq (configurable, default diset saat F2).
- Debounce/cooldown command Telegram `/analyze` per user.
- Retry budget LLM dari TradingAgents config dipertahankan; cache hasil
  analisis per (ticker, timeframe, window) dengan TTL dari settings.

## 6. Tooling Gate Kualitas

- `ruff check` + `ruff format` wajib bersih di `packages/seith-core` (config
  di pyproject core; diperluas ke apps saat masing-masing lahir kodenya).
- mypy strict menyusul di F6 (hardening) - dicatat agar tidak terlupa.
- Secret scanning (gitleaks) menyusul saat repo di-push remote pertama kali.
