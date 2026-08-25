"""Unit test: bot chat-endpoint resolution (pure, no network/Telegram)."""
from seith_core.config import LLMSettings

from seith_api.bot import _resolve_chat_endpoint


def test_endpoint_uses_router_when_set() -> None:
    llm = LLMSettings(router_base_url="http://localhost:20128/v1")
    assert _resolve_chat_endpoint(llm) == "http://localhost:20128/v1/chat/completions"


def test_endpoint_trims_trailing_slash() -> None:
    llm = LLMSettings(router_base_url="http://localhost:20128/v1/")
    assert _resolve_chat_endpoint(llm) == "http://localhost:20128/v1/chat/completions"


def test_endpoint_falls_back_to_openrouter_without_router() -> None:
    llm = LLMSettings(provider="groq")
    assert _resolve_chat_endpoint(llm) == "https://openrouter.ai/api/v1/chat/completions"


def test_endpoint_never_contains_bearer_key() -> None:
    llm = LLMSettings(router_base_url="http://localhost:20128/v1")
    ep = _resolve_chat_endpoint(llm)
    assert "Bearer" not in ep
    assert "api_key" not in ep
