"""SEITH Core API hub - REST + WebSocket fan-out (PRD FR-E5, NFR-Portability).

Bind default 127.0.0.1 (ADR-0002 §3). Menjadi sumber data resmi dashboard &
klien lain; file-based polling di dashboard tetap jalan sebagai fallback.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from seith_core.config import get_settings
from seith_core.schemas import OrderProposalStatus as Status
from seith_trader import proposals, risk

app = FastAPI(title="SEITH Core API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3100"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
def status() -> dict:
    settings = get_settings()
    decisions_dir = Path(settings.data_dir) / "decisions"
    return {
        "environment": settings.environment,
        "halt": risk.is_halted(settings),
        "llm_provider": settings.llm.provider,
        "quick_model": settings.llm.quick_model,
        "deep_model": settings.llm.deep_model,
        "channel_broadcast": settings.telegram.channel_configured,
        "decisions_count": len(list(decisions_dir.glob("*.json"))) if decisions_dir.exists() else 0,
        "pending_proposals": len(proposals.list_by_status(Status.PENDING_APPROVAL, settings)),
        "approved_proposals": len(proposals.list_by_status(Status.APPROVED, settings)),
    }


@app.get("/api/decisions")
def decisions(limit: int = 20) -> dict:
    if not 1 <= limit <= 100:
        raise HTTPException(400, "limit 1..100")
    from seith_analysis.decision_store import load_recent

    return {"decisions": load_recent(limit=limit)}


@app.get("/api/proposals")
def list_props(status: str = "PENDING_APPROVAL") -> dict:
    try:
        st = Status(status.lower())
    except ValueError as exc:
        raise HTTPException(400, f"status '{status}' tidak valid") from exc
    items = proposals.list_by_status(st)
    return {
        "proposals": [
            {"proposal_id": p.proposal_id, "ticker": p.ticker, "side": p.side.value,
             "quantity": str(p.quantity), "status": p.status.value}
            for p in items
        ]
    }


@app.get("/api/backtests")
def backtests() -> dict:
    settings = get_settings()
    base = Path(settings.data_dir) / "backtests"
    out: list[dict] = []
    if base.exists():
        for stats_path in base.glob("*/*/*/stats.json") or []:
            try:
                out.append(json.loads(stats_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
    # fallback glob pattern lama (ticker/tf/stats.json)
    if not out and base.exists():
        for stats_path in base.glob("*/*/stats.json"):
            try:
                out.append(json.loads(stats_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
    return {"backtests": out}


@app.get("/api/tearsheet")
def tearsheet(ticker: str, tf: str = "1h") -> dict:
    if not _safe_component(ticker) or not _safe_component(tf):
        raise HTTPException(400, "parameter tidak valid")
    settings = get_settings()
    path = Path(settings.data_dir) / "backtests" / ticker.upper() / tf / "tearsheet.html"
    if not path.exists():
        raise HTTPException(404, "tearsheet tidak ditemukan")
    return {"path": str(path), "size_bytes": path.stat().st_size}


def _safe_component(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"[A-Za-z0-9._-]{1,20}", value)) and ".." not in value


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """Fan-out periodik status - pola dasar realtime; event-driven menyusul P6."""
    await websocket.accept()
    try:
        while True:
            payload = json.dumps({"type": "status", "data": status()})
            await websocket.send_text(payload)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        return


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host="127.0.0.1", port=20130, log_level="info")


if __name__ == "__main__":
    main()
