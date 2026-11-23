"""Sesgo agregado de la cesta de ballenas por activo."""

from __future__ import annotations

from collections import deque
from typing import Iterable

from .state import Book, WalletInfo


def compute_bias(book: Book, wallets: dict[str, WalletInfo], marks: dict[str, float],
                 threshold: float = 0.25) -> dict[str, dict]:
    """Por coin: notional largo/corto, amplitud y sesgo ponderado por la puntuación de cada wallet.

    - breadth  = (wallets largas − cortas) / total          ∈ [−1, 1]
    - bias     = Σ w·signo·notional / Σ w·notional           ∈ [−1, 1]   (w = score de la wallet)
    - label    = LARGO si bias ≥ umbral, CORTO si ≤ −umbral, si no NEUTRAL
    """
    acc: dict[str, dict] = {}
    for (addr, coin), pos in book.positions.items():
        info = wallets.get(addr)
        if info is None:
            continue
        mark = marks.get(coin) or pos.avg_entry
        notional = abs(pos.szi) * mark
        if notional <= 0:
            continue
        a = acc.setdefault(coin, {"long_usd": 0.0, "short_usd": 0.0, "n_long": 0, "n_short": 0,
                                  "w_signed": 0.0, "w_total": 0.0, "top": []})
        sign = 1 if pos.szi > 0 else -1
        if sign > 0:
            a["long_usd"] += notional
            a["n_long"] += 1
        else:
            a["short_usd"] += notional
            a["n_short"] += 1
        a["w_signed"] += info.score * sign * notional
        a["w_total"] += info.score * notional
        a["top"].append({"address": addr, "side": pos.side, "notional": notional, "entry": pos.avg_entry,
                         "lev": pos.leverage, "liq_px": pos.liq_px, "upnl": pos.upnl, "score": info.score})

    out: dict[str, dict] = {}
    for coin, a in acc.items():
        n = a["n_long"] + a["n_short"]
        gross = a["long_usd"] + a["short_usd"]
        bias = a["w_signed"] / a["w_total"] if a["w_total"] else 0.0
        out[coin] = {
            "coin": coin,
            "mark": marks.get(coin),
            "long_usd": a["long_usd"], "short_usd": a["short_usd"],
            "net_usd": a["long_usd"] - a["short_usd"], "gross_usd": gross,
            "n_long": a["n_long"], "n_short": a["n_short"],
            "breadth": (a["n_long"] - a["n_short"]) / n if n else 0.0,
            "bias": bias,
            "label": "LARGO" if bias >= threshold else "CORTO" if bias <= -threshold else "NEUTRAL",
            "top": sorted(a["top"], key=lambda t: t["notional"], reverse=True)[:8],
        }
    return out


class BiasHistory:
    """Serie temporal del sesgo (para medir el cambio en 1h/4h/24h, que importa más que el nivel)."""

    def __init__(self, max_points: int = 1500):
        self._pts: deque[tuple[float, dict[str, tuple[float, float]]]] = deque(maxlen=max_points)

    def add(self, ts: float, bias: dict[str, dict]) -> None:
        self._pts.append((ts, {c: (b["bias"], b["net_usd"]) for c, b in bias.items()}))

    def delta(self, coin: str, now: float, seconds: float) -> dict[str, float] | None:
        """Cambio de sesgo y de net_usd frente al punto más cercano a `now − seconds`."""
        target = now - seconds
        ref = None
        for ts, snap in self._pts:
            if ts <= target:
                ref = snap
            else:
                break
        if ref is None or coin not in ref or not self._pts:
            return None
        cur = self._pts[-1][1].get(coin)
        if cur is None:
            return None
        return {"bias": cur[0] - ref[coin][0], "net_usd": cur[1] - ref[coin][1]}

    def series(self, coin: str) -> list[tuple[float, float]]:
        return [(ts, snap[coin][0]) for ts, snap in self._pts if coin in snap]


def pinned_first(coins: Iterable[str], pinned: tuple[str, ...] = ("BTC", "ETH")) -> list[str]:
    cs = list(coins)
    return [c for c in pinned if c in cs] + [c for c in cs if c not in pinned]
