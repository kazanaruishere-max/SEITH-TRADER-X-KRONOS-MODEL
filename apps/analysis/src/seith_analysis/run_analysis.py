"""Orchestrator pipeline analisis SEITH.

data segar -> Kronos forecast -> debat multi-agent TradingAgents (Groq)
-> Decision terstandar -> persist SQLite + JSON.

Entry CLI: python -m seith_analysis.run_analysis --ticker BTCUSDT
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import UTC, datetime

from pydantic import TypeAdapter
from seith_core.config import get_settings
from seith_core.schemas import (
    AgentReport,
    AssetClass,
    Decision,
    Ticker,
    Timeframe,
)

from seith_analysis.decision_store import export_json, save_decision
from seith_analysis.rating_map import blend_confidence, map_rating_to_action

logger = logging.getLogger("seith.analysis")

_validate_ticker = TypeAdapter(Ticker)

_ASSET_TYPE = {
    AssetClass.CRYPTO: "crypto",
    AssetClass.EQUITY_US: "stock",
    AssetClass.FOREX: "stock",  # forex lewat pipeline stock (Yahoo FX symbols)
}

_STATE_REPORTS = {
    "market_report": ("market_analyst", "analyst"),
    "sentiment_report": ("sentiment_analyst", "analyst"),
    "news_report": ("news_analyst", "analyst"),
    "fundamentals_report": ("fundamentals_analyst", "analyst"),
    "trader_investment_plan": ("trader", "trader"),
}


_PROVIDER_ENV_VAR = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _ensure_llm_env() -> None:
    """Boundary konstruksi client: satu-satunya tempat secret value dipakai."""
    llm = get_settings().llm
    env_var = _PROVIDER_ENV_VAR.get(llm.provider)
    if env_var is None:
        known = ", ".join(sorted(_PROVIDER_ENV_VAR))
        raise RuntimeError(f"LLM provider '{llm.provider}' tidak dikenal; pilih dari {known}")
    if not llm.api_key:
        raise RuntimeError("SEITH_LLM__API_KEY belum dikonfigurasi")
    os.environ[env_var] = llm.api_key.get_secret_value()


def _extract_rating(decision_obj: object, final_trade_decision: str) -> str:
    rating = getattr(decision_obj, "rating", None)
    if rating:
        return str(rating)
    from tradingagents.agents.utils.rating import parse_rating

    return parse_rating(final_trade_decision)


def run_analysis(
    ticker: str,
    timeframe: Timeframe = Timeframe.H1,
    horizon_bars: int = 24,
    debate_rounds: int = 1,
) -> Decision:
    safe_ticker = _validate_ticker.validate_python(ticker)
    settings = get_settings()
    _ensure_llm_env()

    # 1. Data segar (incremental 2 hari cukup untuk konteks intraday)
    from seith_data.backfill import backfill

    logger.info("[%s] refresh data %s...", safe_ticker, timeframe.value)
    backfill(safe_ticker, timeframe.value, days=2)

    # 2. Kronos forecast (GPU lokal)
    from seith_analysis.kronos_service import forecast

    logger.info("[%s] Kronos forecast x%d...", safe_ticker, horizon_bars)
    fc = forecast(safe_ticker, timeframe, horizon_bars)

    # 3. Debat multi-agent
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    cfg = DEFAULT_CONFIG.copy()
    llm = settings.llm
    cfg.update(
        llm_provider=llm.provider,
        deep_think_llm=llm.deep_model,
        quick_think_llm=llm.quick_model,
        max_debate_rounds=debate_rounds,
        max_risk_discuss_rounds=1,
        openai_reasoning_effort="low" if "gpt-oss" in llm.quick_model else None,
    )
    asset_class = fc.asset_class
    trade_date = datetime.now(UTC).date().isoformat()

    logger.info("[%s] debat multi-agent (%s)...", safe_ticker, asset_type_of(asset_class))
    ta = TradingAgentsGraph(debug=False, config=cfg)
    final_state, pm_decision = ta.propagate(
        safe_ticker, trade_date, asset_type=asset_type_of(asset_class)
    )

    # 4. Komposisi Decision terstandar
    rating = _extract_rating(pm_decision, final_state["final_trade_decision"])
    action = map_rating_to_action(rating)
    confidence = blend_confidence(0.8, action, fc.expected_return)

    reports = [
        AgentReport(agent_name=name, role=role, content=str(final_state.get(key, "")))
        for key, (name, role) in _STATE_REPORTS.items()
        if final_state.get(key)
    ]
    reports.append(
        AgentReport(
            agent_name="kronos",
            role="forecast",
            content=(
                f"Forecast {timeframe.value} x{horizon_bars}: expected_return="
                f"{fc.expected_return:+.4f}, confidence={fc.confidence}, "
                f"forecast_id={fc.forecast_id}"
            ),
            verdict=f"{fc.expected_return:+.2%}",
        )
    )

    decision = Decision(
        ticker=safe_ticker,
        asset_class=asset_class,
        trade_date=trade_date,
        action=action,
        confidence=confidence,
        reasoning_summary=str(pm_decision) if pm_decision else rating,
        reports=tuple(reports),
        risk_assessment=str(final_state.get("final_trade_decision", rating)),
        forecast_id=fc.forecast_id,
    )

    # 5. Persist
    save_decision(decision)
    json_path = export_json(decision)
    logger.info(
        "[%s] DECISION: %s (conf %.2f) -> %s",
        safe_ticker,
        action.value.upper(),
        confidence,
        json_path,
    )
    return decision


def asset_type_of(asset_class: AssetClass) -> str:
    return _ASSET_TYPE[asset_class]


def main() -> None:
    parser = argparse.ArgumentParser(description="SEITH full analysis pipeline")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--horizon", type=int, default=24)
    parser.add_argument("--debate-rounds", type=int, default=1)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")

    decision = run_analysis(
        args.ticker, Timeframe(args.timeframe), args.horizon, args.debate_rounds
    )
    print(decision.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
