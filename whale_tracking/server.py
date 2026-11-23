"""API FastAPI + panel web estático."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse

from .config import Settings
from .engine import Engine

WEB = Path(__file__).resolve().parent.parent / "web"


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

    app = FastAPI(title="Whale Tracking", version="0.1.0", lifespan=lifespan)
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
        return [{"t": t, "bias": b} for t, b in engine.history.series(coin.upper())]

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
