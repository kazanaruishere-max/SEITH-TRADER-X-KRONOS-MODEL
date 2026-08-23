"""Pemetaan rating 5-tier TradingAgents -> Action SEITH + blending confidence.

Heuristik confidence fase awal: baseline per rating, digeser bila arah Kronos
sejalan/kontradiktif. Diganti kalibrasi empiris setelah baseline paper 30 hari.
"""

from __future__ import annotations

from seith_core.schemas import Action

# Rating 5-tier vendor: Buy / Overweight / Hold / Underweight / Sell
RATING_TO_ACTION: dict[str, Action] = {
    "buy": Action.BUY,
    "overweight": Action.BUY,
    "hold": Action.HOLD,
    "underweight": Action.SELL,
    "sell": Action.SELL,
}

_BASE_CONFIDENCE = {Action.BUY: 0.65, Action.HOLD: 0.50, Action.SELL: 0.65}
_KRONOS_AGREE_BONUS = 0.10
_KRONOS_CONTRADICT_PENALTY = 0.20


def map_rating_to_action(rating: str) -> Action:
    try:
        return RATING_TO_ACTION[rating.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"rating tidak dikenal: '{rating}'") from exc


def kronos_agrees(action: Action, expected_return: float) -> bool | None:
    if action is Action.HOLD:
        return None
    if abs(expected_return) < 0.001:
        return None
    bullish = (action is Action.BUY and expected_return > 0) or (
        action is Action.SELL and expected_return < 0
    )
    return bullish


def blend_confidence(rating_confidence: float, action: Action, expected_return: float) -> float:
    """Gabungkan confidence PM dengan sinyal Kronos menjadi satu angka [0.05, 0.95]."""
    base = _BASE_CONFIDENCE[action]
    shifted = base * rating_confidence
    agree = kronos_agrees(action, expected_return)
    if agree is True:
        shifted += _KRONOS_AGREE_BONUS
    elif agree is False:
        shifted -= _KRONOS_CONTRADICT_PENALTY
    return round(min(0.95, max(0.05, shifted)), 4)
