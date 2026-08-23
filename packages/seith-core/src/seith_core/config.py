"""Application settings & secrets loading.

Semua secret dibaca dari environment variables / file `.env` dengan prefix `SEITH_`.
Nested settings pakai double-underscore delimiter, contoh: `SEITH_LLM__API_KEY`.

Aturan keamanan:
- Secret bertipe `SecretStr` agar tidak bocor saat repr/log.
- File `.env` tidak pernah di-commit (lihat .gitignore).
- Lokasi `.env` default relatif terhadap CWD; untuk determinisme lintas service
  set `SEITH_ENV_FILE` ke path absolut (direkomendasikan saat deploy).
- Unknown env var berprefix `SEITH_` = ValidationError keras saat konstruksi
  (fail-fast; typo secret tidak boleh menjadi silent-unconfigured).
  `detect_unknown_seith_env_vars()` tetap tersedia untuk preflight diagnostik.
- Guard kombinasi berbahaya (live tanpa approval/kredensial) ditegakkan oleh
  validator di level AppSettings - tidak bisa dilewati dari luar.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from seith_core.schemas import RiskLimits

PARQUET_DIRNAME = "parquet"


class _FrozenModel(BaseModel):
    """Base nested settings: immutable & strict, konsisten dengan domain schemas."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class LLMSettings(_FrozenModel):
    """Provider LLM pluggable: 'groq' | 'openrouter' (lihat run_analysis._ensure_llm_env).

    Model names harus valid di katalog provider masing-masing pada saat runtime;
    katalog berubah cepat - verifikasi via API sebelum mengganti.
    """

    api_key: SecretStr | None = None
    provider: str = "groq"
    quick_model: str = "openai/gpt-oss-20b"
    deep_model: str = "openai/gpt-oss-120b"
    max_retries: int = 3
    cache_ttl_seconds: int = 3600


class TelegramSettings(_FrozenModel):
    """Bot personal = kontrol plane (approve/halt/report).

    `channel_id` opsional untuk broadcast digest komunitas (satu arah,
    konten tersanitasi - tidak pernah berisi detail akun sensitif).
    """

    bot_token: SecretStr | None = None
    allowed_user_ids: tuple[int, ...] = ()
    channel_id: int | None = None

    @property
    def configured(self) -> bool:
        """Bot hanya boleh start jika allowlist user eksplisit (fail-closed)."""
        return self.bot_token is not None and len(self.allowed_user_ids) > 0

    @property
    def channel_configured(self) -> bool:
        return self.bot_token is not None and self.channel_id is not None


class BinanceSettings(_FrozenModel):
    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None

    @property
    def configured(self) -> bool:
        return self.api_key is not None and self.api_secret is not None


class OandaSettings(_FrozenModel):
    """Kosakata environment OANDA: 'practice' | 'live'.

    Mapping disengaja BERBEDA dari AppSettings.environment ('dev'|'paper'|'live'):
    global paper/dev memaksa OANDA practice; OANDA live hanya boleh saat
    environment global juga 'live' (ditegakkan validator AppSettings).
    """

    access_token: SecretStr | None = None
    account_id: str | None = None
    environment: Literal["practice", "live"] = "practice"

    @property
    def base_url(self) -> str:
        host = "api-fxtrade" if self.environment == "live" else "api-fxpractice"
        return f"https://{host}.oanda.com"


class KronosSettings(_FrozenModel):
    model_name: str = "NeoQuasar/Kronos-base"
    tokenizer_name: str = "NeoQuasar/Kronos-Tokenizer-base"
    device: Literal["cuda", "cpu", "auto"] = "auto"


def _unwrap_model(annotation: object) -> type[BaseModel] | None:
    """Ambil subclass BaseModel dari annotation (termasuk Optional[X])."""
    candidates: tuple[object, ...]
    if get_origin(annotation) is not None:
        candidates = get_args(annotation)
    else:
        candidates = (annotation,)
    for candidate in candidates:
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _collect_known_env_names(model: type[BaseModel], prefix: str = "SEITH_") -> set[str]:
    names: set[str] = set()
    for name, field in model.model_fields.items():
        env_key = f"{prefix}{name.upper()}"
        names.add(env_key)
        sub = _unwrap_model(field.annotation)
        if sub is not None:
            names |= _collect_known_env_names(sub, f"{env_key}__")
    return names


def detect_unknown_seith_env_vars() -> tuple[str, ...]:
    """Env var berprefix SEITH_ yang tidak cocok field manapun (indikasi typo)."""
    known = _collect_known_env_names(AppSettings)
    actual = {key for key in os.environ if key.startswith("SEITH_")}
    return tuple(sorted(actual - known))


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEITH_",
        env_file=".env",
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    environment: Literal["dev", "paper", "live"] = "dev"
    data_dir: Path = Path("data")
    db_path: Path = Path("data/seith.db")

    llm: LLMSettings = LLMSettings()
    telegram: TelegramSettings = TelegramSettings()
    binance: BinanceSettings = BinanceSettings()
    oanda: OandaSettings = OandaSettings()
    kronos: KronosSettings = KronosSettings()
    risk: RiskLimits = RiskLimits()

    @model_validator(mode="after")
    def _guard_environment_combination(self) -> AppSettings:
        """Tolak kombinasi config yang membunuh diri sendiri (fail-fast)."""
        if self.environment == "live":
            if not self.binance.configured:
                raise ValueError("environment='live' memerlukan kredensial Binance terkonfigurasi")
            if not self.risk.require_approval:
                raise ValueError(
                    "risk.require_approval=false DILARANG saat environment='live' "
                    "(approval gate manusia wajib, lihat PRD FR-E3)"
                )
        elif self.oanda.environment == "live":
            raise ValueError("oanda.environment='live' hanya boleh saat environment='live'")
        return self

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / PARQUET_DIRNAME).mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def validate_startup(self) -> None:
        """Preflight entrypoint: diagnostik env + placeholder guard masa depan.

        Unknown env var sudah ditolak keras oleh `extra="forbid"` saat
        konstruksi; fungsi ini tetap ada sebagai titik pemeriksaan eksplisit
        untuk guard yang butuh konteks runtime (mis. cek file, jaringan).
        """
        detect_unknown_seith_env_vars()


def _env_file_path() -> str | Path:
    override = os.environ.get("SEITH_ENV_FILE")
    return Path(override) if override else ".env"


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings(_env_file=_env_file_path())


def reset_settings_cache() -> None:
    """Untuk testing / reload konfigurasi antar-proses panjang."""
    get_settings.cache_clear()
