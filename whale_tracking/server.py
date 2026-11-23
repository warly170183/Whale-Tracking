"""API FastAPI + panel web estático."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .config import Settings
from .engine import Engine

WEB = Path(__file__).resolve().parent.parent / "web"


class EntryIn(BaseModel):
    coin: str
    direction: str                  # long | short
    entry_px: float | None = None   # por defecto, el mark actual
    risk_pct: float | None = None   # distancia al stop en % (para calcular R)
    note: str = ""


class CloseIn(BaseModel):
    exit_px: float | None = None


def create_app(cfg: Settings | None = None) -> FastAPI:
    cfg = cfg or Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    engine = Engine(cfg)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await engine.start()
        yield
        await engine.stop()

    app = FastAPI(title="Whale Tracking", version="0.2.0", lifespan=lifespan)
    app.state.engine = engine

    @app.get("/api/health")
    def health():
        return {"ok": True, "phase": engine.status["phase"], "ws": engine.status["ws_connected"]}

    @app.get("/api/state")
    def state():
        return engine.snapshot()

    @app.get("/api/wallets")
    def wallets():
        return engine.wallet_rows()

    @app.get("/api/events")
    def events(limit: int = Query(100, ge=1, le=500)):
        return list(engine.events)[:limit]

    @app.get("/api/history/{coin}")
    def history(coin: str):
        name = engine._canon(coin, engine.marks) or coin
        return [{"t": t, "bias": b} for t, b in engine.history.series(name, max_points=300)]

    @app.get("/api/signals")
    def signals(min_wallets: int = Query(3, ge=1, le=50)):
        """Señales de contexto por activo, listas para consumir desde otras apps."""
        return {"as_of": time.time(), "stale": engine.stale(), "signals": engine.signals(min_wallets)}

    @app.get("/api/signal/{coin}")
    def signal(coin: str):
        s = engine.signal(coin)
        if s is None:
            raise HTTPException(404, f"sin posiciones de ballenas seguidas en {coin.upper()}")
        return s

    @app.get("/api/liqmap/{coin}")
    def liqmap(coin: str):
        m = engine.liq_map(coin)
        if m is None:
            raise HTTPException(404, f"sin precio para {coin.upper()}")
        return m

    @app.get("/api/alignment/{coin}")
    def alignment(coin: str, direction: str = Query(..., pattern="^(long|short)$")):
        a = engine.alignment(coin, direction)
        if a is None:
            raise HTTPException(404, f"sin posiciones de ballenas seguidas en {coin.upper()}")
        return a

    @app.post("/api/journal")
    def journal_add(body: EntryIn):
        if body.direction not in ("long", "short"):
            raise HTTPException(422, "direction debe ser long o short")
        a = engine.alignment(body.coin, body.direction)
        if a is None:
            raise HTTPException(404, f"sin datos de ballenas para {body.coin.upper()}")
        name = a["coin"]            # nombre canónico (p. ej. kPEPE)
        px = body.entry_px or engine.marks.get(name)
        if not px:
            raise HTTPException(422, "sin precio: indica entry_px")
        return engine.journal.add(name, body.direction, px, a, a["signal"], body.risk_pct, body.note)

    @app.post("/api/journal/{entry_id}/close")
    def journal_close(entry_id: str, body: CloseIn):
        entry = next((e for e in engine.journal.entries if e["id"] == entry_id), None)
        px = body.exit_px or (engine.marks.get(entry["coin"]) if entry else None)
        if not px:
            raise HTTPException(422, "sin precio: indica exit_px")
        e = engine.journal.close(entry_id, px)
        if e is None:
            raise HTTPException(404, "entrada no encontrada o ya cerrada")
        return e

    @app.get("/api/journal")
    def journal_list():
        return list(reversed(engine.journal.entries))

    @app.get("/api/journal/stats")
    def journal_stats():
        return engine.journal.stats()

    @app.get("/api/stream")
    async def stream():
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        engine.subscribers.add(q)

        async def gen():
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(ev)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                engine.subscribers.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    return app


app = create_app()
