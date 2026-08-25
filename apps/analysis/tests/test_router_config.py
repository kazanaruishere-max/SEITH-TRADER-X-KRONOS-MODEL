"""Unit test: build_ta_config — router-aware (pure, no TradingAgents run).

Tidak butuh jaringan/GPU/Grok; hanya verifikasi logika pemilihan provider +
backend_url antara 9router (keyless) vs provider per-key.
"""
from seith_core.config import LLMSettings

from seith_analysis.run_analysis import build_ta_config


def test_router_url_parsed() -> None:
    s = LLMSettings(router_base_url="http://localhost:20128/v1")
    assert str(s.router_base_url) == "http://localhost:20128/v1"


def test_router_url_default_none() -> None:
    assert LLMSettings().router_base_url is None


def test_build_config_router_mode_is_keyless() -> None:
    llm = LLMSettings(
        router_base_url="http://localhost:20128/v1",
        deep_model="nvidia/nemotron-3.5-lightning:free",
        quick_model="nvidia/nemotron-3.5-lightning:free",
    )
    cfg = build_ta_config(llm=llm)
    assert cfg["llm_provider"] == "openai_compatible"
    assert cfg["backend_url"] == "http://localhost:20128/v1"
    assert cfg["deep_think_llm"] == "nvidia/nemotron-3.5-lightning:free"
    assert cfg["quick_think_llm"] == "nvidia/nemotron-3.5-lightning:free"


def test_build_config_provider_key_mode_uses_provider() -> None:
    llm = LLMSettings(provider="openrouter")
    cfg = build_ta_config(llm=llm)
    assert cfg["llm_provider"] == "openrouter"


def test_build_config_reasoning_effort_flagged() -> None:
    llm = LLMSettings(
        provider="openrouter",
        quick_model="openai/gpt-oss-20b",
        deep_model="openai/gpt-oss-120b",
    )
    cfg = build_ta_config(llm=llm)
    assert cfg["openai_reasoning_effort"] == "low"


def test_build_config_disabled_reasoning_when_not_gpt_oss() -> None:
    llm = LLMSettings(
        provider="openrouter",
        quick_model="nvidia/nemotron-3.5-lightning",
        deep_model="nvidia/nemotron-3.5-lightning",
    )
    cfg = build_ta_config(llm=llm)
    assert cfg["openai_reasoning_effort"] is None
