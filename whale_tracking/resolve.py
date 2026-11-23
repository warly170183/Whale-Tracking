"""Helpers puros (sin I/O): resolución de trades públicos y semillas desde clearinghouseState.

La convención users == [comprador, vendedor] y el patrón de resolución siguen el diseño de
KonScanner/hyperliquid-trader-tracker (MIT). Ver THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .state import Position


@dataclass(slots=True)
class ResolvedFill:
    address: str
    coin: str
    delta: float   # +sz si la wallet compró, -sz si vendió
    px: float
    ts: float      # epoch s


def perp_coins_from_meta(meta: Any) -> list[str]:
    universe = meta.get("universe", []) if isinstance(meta, dict) else []
    return [u["name"] for u in universe if isinstance(u, dict) and u.get("name") and not u.get("isDelisted")]


def resolve_deltas(trade: dict[str, Any], watch: frozenset[str] | set[str]) -> list[ResolvedFill]:
    """Un trade público => deltas firmados de las wallets vigiladas que participaron."""
    coin = trade.get("coin")
    users = trade.get("users")
    if not coin or not isinstance(users, list) or len(users) != 2:
        return []
    try:
        px = float(trade["px"])
        sz = float(trade["sz"])
    except (KeyError, TypeError, ValueError):
        return []
    if sz == 0:
        return []
    raw = trade.get("time")
    ts = raw / 1000 if raw is not None else time.time()
    buyer, seller = str(users[0]).lower(), str(users[1]).lower()
    fills = []
    if buyer in watch:
        fills.append(ResolvedFill(buyer, coin, sz, px, ts))
    if seller in watch:
        fills.append(ResolvedFill(seller, coin, -sz, px, ts))
    return fills


def positions_from_clearinghouse(address: str, state: dict[str, Any], now: float | None = None
                                 ) -> tuple[float, list[Position]]:
    """(valor de cuenta, posiciones abiertas) desde una respuesta `clearinghouseState`."""
    now = now or time.time()
    try:
        account_value = float(state.get("marginSummary", {}).get("accountValue", 0))
    except (TypeError, ValueError):
        account_value = 0.0
    out: list[Position] = []
    for ap in state.get("assetPositions", []):
        p = ap.get("position", {})
        coin = p.get("coin")
        try:
            szi = float(p.get("szi", 0))
        except (TypeError, ValueError):
            continue
        if not coin or szi == 0:
            continue

        def _f(key: str) -> float | None:
            v = p.get(key)
            try:
                return float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None

        lev = (p.get("leverage") or {}).get("value")
        out.append(Position(
            address=address, coin=coin, szi=szi, avg_entry=_f("entryPx") or 0.0, opened_at=now,
            leverage=int(lev) if lev else None, liq_px=_f("liquidationPx"), upnl=_f("unrealizedPnl"),
        ))
    return account_value, out
