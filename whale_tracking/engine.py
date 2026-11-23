"""Motor: selección → semilla → WebSocket en vivo → sesgo agregado."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from typing import Any

import websockets

from .aggregate import EXCLUDED_DEFAULT, BiasHistory, build_liq_map, compute_bias, pinned_first
from .classify import classify
from .config import Settings
from .hl_client import InfoClient
from .ranking import apply_portfolio, select_watchlist
from .resolve import perp_coins_from_meta, positions_from_clearinghouse, resolve_deltas
from .state import Book, Event, SeenTids, WalletInfo

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.client = InfoClient(cfg.info_url)
        self.book = Book()
        self.wallets: dict[str, WalletInfo] = {}
        self.watch: frozenset[str] = frozenset()
        self.marks: dict[str, float] = {}
        self.history = BiasHistory()
        self.exclude = frozenset() if cfg.include_mm else EXCLUDED_DEFAULT
        self.events: deque[dict] = deque(maxlen=500)
        self.subscribers: set[asyncio.Queue] = set()
        self.seen = SeenTids()
        self.status = {"phase": "init", "detail": "", "started": time.time(), "ws_connected": False,
                       "last_trade_ts": None, "sharpe_done": 0, "seeded": 0}
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    # ── ciclo de vida ────────────────────────────────────────────────────────
    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._run(), name="engine"))

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._save_history()
        await self.client.close()

    async def _run(self) -> None:
        try:
            self._phase("ranking", "descargando leaderboard")
            rows = await self.client.get_json(self.cfg.leaderboard_url)
            rows = rows.get("leaderboardRows", rows) if isinstance(rows, dict) else rows
            chosen = select_watchlist(rows, self.cfg)
            self.wallets = {w.address: w for w in chosen}
            self.watch = frozenset(self.wallets)
            log.info("watchlist: %d wallets", len(self.wallets))

            self._phase("seeding", f"cargando posiciones de {len(self.wallets)} wallets")
            await self._seed_all()
            self.refresh_styles()
            self._load_history()

            self._phase("live", "")
            loops = [self._listener(), self._reconcile_loop(), self._history_loop()]
            if self.cfg.sharpe_enabled:
                loops.append(self._sharpe_loop())
            await asyncio.gather(*loops)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("engine caído")
            self._phase("error", str(e))

    def _phase(self, phase: str, detail: str) -> None:
        self.status["phase"], self.status["detail"] = phase, detail

    # ── semilla / reconciliación ─────────────────────────────────────────────
    async def _seed_wallet(self, addr: str, guard: bool) -> None:
        epoch = self.book.epoch(addr) if guard else None
        state = await self.client.info({"type": "clearinghouseState", "user": addr})
        acct, positions = positions_from_clearinghouse(addr, state)
        if self.book.reseed(addr, positions, expected_epoch=epoch):
            info = self.wallets[addr]
            info.account_value = acct or info.account_value
            if not info.seeded:
                info.seeded = True
                self.status["seeded"] += 1

    async def _seed_all(self, guard: bool = False) -> None:
        async def one(a: str) -> None:
            try:
                await self._seed_wallet(a, guard)
            except Exception as e:  # noqa: BLE001
                log.warning("seed %s falló: %s", a[:10], e)

        await asyncio.gather(*(one(a) for a in self.wallets))

    async def _reconcile_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.cfg.reconcile_s)
            await self._seed_all(guard=True)
            self.refresh_styles()

    # ── WebSocket ────────────────────────────────────────────────────────────
    async def _listener(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._connection()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.status["ws_connected"] = False
                log.warning("WS caído (%s); reintento en %.0fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _connection(self) -> None:
        coins = list(self.cfg.coins) or perp_coins_from_meta(await self.client.info({"type": "meta"}))
        if not coins:
            raise RuntimeError("meta sin coins")
        async with websockets.connect(self.cfg.ws_url, ping_interval=None, max_size=None) as ws:
            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "allMids"}}))
            for c in coins:
                await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))
            self.status["ws_connected"] = True
            log.info("WS suscrito a %d coins", len(coins))

            async def ping() -> None:
                while True:
                    await asyncio.sleep(25)   # HL cierra conexiones inactivas a los 60 s
                    await ws.send(json.dumps({"method": "ping"}))

            pinger = asyncio.create_task(ping())
            try:
                async for raw in ws:
                    self._on_message(raw)
            finally:
                pinger.cancel()
            raise ConnectionError("WS cerrado")

    def _on_message(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        ch = msg.get("channel")
        if ch == "trades":
            self.status["last_trade_ts"] = time.time()
            for t in msg.get("data") or []:
                self._on_trade(t)
        elif ch == "allMids":
            for coin, px in ((msg.get("data") or {}).get("mids") or {}).items():
                try:
                    self.marks[coin] = float(px)
                except (TypeError, ValueError):
                    pass

    def _on_trade(self, trade: dict[str, Any]) -> None:
        fills = resolve_deltas(trade, self.watch)
        if not fills:
            return
        tid = trade.get("tid")
        if isinstance(tid, int) and self.seen.check_and_add(tid):
            return
        for f in fills:
            for ev in self.book.ingest(address=f.address, coin=f.coin, delta=f.delta, px=f.px, ts=f.ts):
                self._publish(ev)

    def _publish(self, ev: Event) -> None:
        if ev.notional < self.cfg.event_min_usd:
            return
        d = ev.as_dict()
        info = self.wallets.get(ev.address)
        d["score"] = info.score if info else None
        d["account_value"] = info.account_value if info else None
        self.events.appendleft(d)
        for q in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(d)

    # ── series y Sharpe ──────────────────────────────────────────────────────
    async def _history_loop(self) -> None:
        last_save = time.time()
        while not self._stop.is_set():
            self.history.add(time.time(), self.bias())
            if time.time() - last_save >= 300:
                self._save_history()
                last_save = time.time()
            await asyncio.sleep(self.cfg.history_s)

    def _history_path(self):
        return self.cfg.data_dir / "history.json"

    def _load_history(self) -> None:
        try:
            self.history.load(json.loads(self._history_path().read_text()), min_ts=time.time() - 25 * 3600)
        except (OSError, ValueError):
            pass

    def _save_history(self) -> None:
        try:
            self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
            self._history_path().write_text(json.dumps(self.history.dump()))
        except OSError as e:
            log.warning("no se pudo guardar el histórico: %s", e)

    def refresh_styles(self) -> None:
        for a, w in self.wallets.items():
            w.style = classify(w, self.book.of_wallet(a), self.marks, self.cfg.mm_turnover)

    def _cache_path(self):
        return self.cfg.data_dir / "sharpe_cache.json"

    def _load_cache(self) -> dict:
        try:
            return json.loads(self._cache_path().read_text())
        except (OSError, ValueError):
            return {}

    async def _sharpe_loop(self) -> None:
        cache = self._load_cache()
        now = time.time()
        order = sorted(self.wallets.values(), key=lambda w: w.account_value, reverse=True)
        for info in order:
            if self._stop.is_set():
                return
            c = cache.get(info.address)
            if c and now - c["t"] < self.cfg.portfolio_cache_ttl_s:
                info.sharpe_30d, info.sharpe_all, info.mdd_30d, info.score = (
                    c["s30"], c["sall"], c["mdd"], c["score"])
                self.status["sharpe_done"] += 1
                continue
            try:
                pf = await self.client.info({"type": "portfolio", "user": info.address})
                apply_portfolio(info, pf)
                cache[info.address] = {"t": time.time(), "s30": info.sharpe_30d, "sall": info.sharpe_all,
                                       "mdd": info.mdd_30d, "score": info.score}
            except Exception as e:  # noqa: BLE001
                log.warning("portfolio %s falló: %s", info.address[:10], e)
            self.status["sharpe_done"] += 1
            await asyncio.sleep(self.cfg.sharpe_req_interval_s)
        try:
            self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path().write_text(json.dumps(cache))
        except OSError as e:
            log.warning("no se pudo guardar la caché de Sharpe: %s", e)

    # ── lecturas ─────────────────────────────────────────────────────────────
    def bias(self) -> dict[str, dict]:
        return compute_bias(self.book, self.wallets, self.marks, self.cfg.bias_threshold, self.exclude)

    def stale(self) -> bool:
        t = self.status["last_trade_ts"]
        return not self.status["ws_connected"] or t is None or time.time() - t > 120

    def signal(self, coin: str, bias: dict[str, dict] | None = None) -> dict | None:
        """Señal compacta y exportable para otras apps. Es CONTEXTO de posicionamiento, no un veredicto."""
        bias = bias or self.bias()
        b = bias.get(coin.upper())
        if not b:
            return None
        now = time.time()
        return {
            "coin": b["coin"], "label": b["label"], "bias": round(b["bias"], 4),
            "confidence": b["confidence"], "confidence_note": "heurístico, sin calibrar",
            "n_wallets": b["n_wallets"], "n_long": b["n_long"], "n_short": b["n_short"],
            "long_usd": round(b["long_usd"]), "short_usd": round(b["short_usd"]), "net_usd": round(b["net_usd"]),
            "breadth": round(b["breadth"], 4), "top1_share": round(b["top1_share"], 3),
            "concentrated": b["concentrated"], "n_market_makers_excluded": b["n_excluded"],
            "delta_1h": self.history.delta(b["coin"], now, 3600),
            "delta_4h": self.history.delta(b["coin"], now, 4 * 3600),
            "delta_24h": self.history.delta(b["coin"], now, 24 * 3600),
            "as_of": now, "stale": self.stale(), "source": "hyperliquid-public", "kind": "context",
        }

    def signals(self, min_wallets: int = 3) -> list[dict]:
        bias = self.bias()
        cs = [c for c in bias if bias[c]["n_wallets"] >= min_wallets]
        cs.sort(key=lambda c: bias[c]["gross_usd"], reverse=True)
        return [self.signal(c, bias) for c in pinned_first(cs)]

    def liq_map(self, coin: str) -> dict | None:
        return build_liq_map(self.book, self.wallets, self.marks, coin.upper(), exclude_styles=self.exclude)

    def snapshot(self) -> dict:
        now = time.time()
        bias = self.bias()
        coins = []
        for c in pinned_first(sorted(bias, key=lambda k: bias[k]["gross_usd"], reverse=True)):
            b = dict(bias[c])
            b["delta_1h"] = self.history.delta(c, now, 3600)
            b["delta_4h"] = self.history.delta(c, now, 4 * 3600)
            b["delta_24h"] = self.history.delta(c, now, 24 * 3600)
            b["spark"] = [round(v, 3) for _, v in self.history.series(c, now - 24 * 3600, 60)]
            coins.append(b)
        return {
            "status": self.status, "now": now, "stale": self.stale(),
            "market_makers": sum(1 for w in self.wallets.values() if w.style == "market_maker"),
            "include_mm": not self.exclude,
            "wallets_tracked": len(self.wallets), "open_positions": len(self.book.positions),
            "coins": coins,
        }

    def wallet_rows(self) -> list[dict]:
        rows = []
        for a, w in self.wallets.items():
            ps = self.book.of_wallet(a)
            gross = sum(abs(p.szi) * (self.marks.get(p.coin) or p.avg_entry) for p in ps)
            net = sum(p.szi * (self.marks.get(p.coin) or p.avg_entry) for p in ps)
            top = max(ps, key=lambda p: abs(p.szi) * (self.marks.get(p.coin) or p.avg_entry), default=None)
            rows.append({
                "address": a, "account_value": w.account_value, "score": w.score,
                "sharpe_30d": w.sharpe_30d, "sharpe_all": w.sharpe_all, "mdd_30d": w.mdd_30d,
                "month_pnl": w.month_pnl, "n_positions": len(ps), "gross_usd": gross, "net_usd": net,
                "top_coin": top.coin if top else None, "top_side": top.side if top else None,
                "custom": w.custom, "style": w.style,
                "turnover": (w.month_vlm / (w.lb_account or w.account_value)) if (w.lb_account or w.account_value) else None,
            })
        rows.sort(key=lambda r: r["gross_usd"], reverse=True)
        return rows
