---
name: seith-dev
description: Workflow development harian SEITH - command cheat-sheet per service, urutan verification gate, troubleshooting environment uv yang sudah terbukti (editable install, venv lokasi, pytest collection). Gunakan saat sesi coding apapun di repo SEITH: menjalankan test, sync dependency, lint, import check, atau debug masalah environment.
---

# SEITH Dev Workflow

## Prinsip utama

1. **Satu venv per direktori proyek** — `packages/seith-core`, `apps/api`,
   `apps/trader`, `apps/analysis` masing-masing proyek uv independen.
2. **Semua perintah dari dalam direktori proyeknya.** Jangan pakai
   `--project`/`-p` dari root.
3. **Klaim selesai hanya dengan output nyata** — paste hasil perintah, jangan
   narasi "seharusnya sudah jalan".

## Cheat sheet

| Aksi | Workdir | Perintah |
|---|---|---|
| Sync deps | proyek terkait | `uv sync` |
| Test core | `packages/seith-core` | `uv run pytest -q` |
| Lint core | root repo | `uvx ruff check packages/seith-core` |
| Format | root repo | `uvx ruff format <path>` |
| Import check api | `apps/api` | `uv run python -c "import seith_api"` |
| Import check trader | `apps/trader` | `uv run python -c "import nautilus_trader"` |
| Import check analysis | `apps/analysis` | `uv run python -c "import vectorbt, tradingagents"` |

## Urutan verification gate (sebelum klaim task done)

1. `uv run pytest -q` di package yang disentuh → semua pass.
2. `uvx ruff check .` → bersih.
3. Import verification env yang terdampak → sukses tanpa traceback.
4. Baru lapor ke user dengan bukti output.

## Troubleshooting (kasus nyata yang sudah pernah terjadi)

### ModuleNotFoundError setelah tambah file baru di src/
Editable install lama tidak memetakan file baru otomatis.

```powershell
uv sync --reinstall-package seith-api    # workdir apps/api (sesuaikan nama pkg)
```

### pytest meng-collect test vendor/ (error import palsu)
Terjadi kalau pytest dipanggil dari root repo. Selalu jalankan dari
direktori package (`workdir=packages/seith-core`) — `testpaths = ["tests"]`
di pyproject-nya yang membatasi scope.

### Semua sync menuang ke .venv root
Gejala: sync app B menghapus package app A (terlihat baris `- paket...`).
Penyebab: menjalankan `uv sync --project X` dari cwd root. Fix: hapus
`.venv` root lalu sync ulang dari dalam tiap direktori proyek.

### Kronos / torch
Torch CUDA hanya ada di env `apps/analysis` dan ditambahkan saat F1
(unduhan besar ~2.5GB). Env lain TIDAK punya torch — jangan import Kronos
model di env trader/api.

## Konvensi cepat

- Python 3.12 via `.python-version`.
- Secret via `.env` (prefix `SEITH_`, nested `__`). Typo env var = gagal keras
  saat konstruksi settings — itu fitur, bukan bug.
- Commit format `<type>: <description>` (feat/fix/refactor/chore/docs/test).
  DILARANG commit tanpa permintaan eksplisit user.
