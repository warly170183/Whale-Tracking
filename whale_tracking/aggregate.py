"""Sesgo agregado de la cesta de ballenas, señal exportable y mapa de liquidaciones."""

from __future__ import annotations

from collections import deque
from typing import Iterable

from .state import Book, WalletInfo

EXCLUDED_DEFAULT = frozenset({"market_maker"})


def _confidence(n: int, top1_share: float, breadth: float, bias: float) -> float:
    """Heurístico 0–1 (SIN calibrar): más wallets, menos concentración, amplitud que coincide con el
    sesgo y sesgo marcado => más confianza en que la lectura no es un artefacto de una sola cuenta."""
    n_f = min(n / 8, 1.0)
    conc_f = 1 - top1_share
    if breadth == 0 or bias == 0:
        agree = 0.6
    else:
        agree = 1.0 if (breadth > 0) == (bias > 0) else 0.3
    strength = min(abs(bias) / 0.6, 1.0)
    return round(0.35 * n_f + 0.25 * conc_f + 0.2 * agree + 0.2 * strength, 3)


def compute_bias(book: Book, wallets: dict[str, WalletInfo], marks: dict[str, float],
                 threshold: float = 0.25, exclude_styles: frozenset[str] = EXCLUDED_DEFAULT
                 ) -> dict[str, dict]:
    """Por coin: notional largo/corto, amplitud y sesgo ponderado por la puntuación de cada wallet.

    - breadth  = (wallets largas − cortas) / total          ∈ [−1, 1]
    - bias     = Σ w·signo·notional / Σ w·notional           ∈ [−1, 1]   (w = score de la wallet)
    - label    = LARGO si bias ≥ umbral, CORTO si ≤ −umbral, si no NEUTRAL
    Las wallets con estilo en `exclude_styles` (market makers) no cuentan; se reportan aparte.
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
                                  "w_signed": 0.0, "w_total": 0.0, "top": [],
                                  "n_excl": 0, "excl_usd": 0.0})
        if info.style in exclude_styles:
            a["n_excl"] += 1
            a["excl_usd"] += notional
            continue
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
                         "lev": pos.leverage, "liq_px": pos.liq_px, "upnl": pos.upnl,
                         "score": info.score, "style": info.style, "w": info.score * notional})

    out: dict[str, dict] = {}
    for coin, a in acc.items():
        n = a["n_long"] + a["n_short"]
        if n == 0:
            continue
        gross = a["long_usd"] + a["short_usd"]
        bias = a["w_signed"] / a["w_total"] if a["w_total"] else 0.0
        breadth = (a["n_long"] - a["n_short"]) / n
        top1 = max((t["w"] for t in a["top"]), default=0.0) / a["w_total"] if a["w_total"] else 0.0
        out[coin] = {
            "coin": coin,
            "mark": marks.get(coin),
            "long_usd": a["long_usd"], "short_usd": a["short_usd"],
            "net_usd": a["long_usd"] - a["short_usd"], "gross_usd": gross,
            "n_long": a["n_long"], "n_short": a["n_short"], "n_wallets": n,
            "breadth": breadth, "bias": bias,
            "label": "LARGO" if bias >= threshold else "CORTO" if bias <= -threshold else "NEUTRAL",
            "top1_share": top1, "concentrated": top1 >= 0.5,
            "confidence": _confidence(n, top1, breadth, bias),
            "n_excluded": a["n_excl"], "excluded_usd": a["excl_usd"],
            "top": [{k: v for k, v in t.items() if k != "w"}
                    for t in sorted(a["top"], key=lambda t: t["notional"], reverse=True)[:8]],
        }
    return out


def build_liq_map(book: Book, wallets: dict[str, WalletInfo], marks: dict[str, float], coin: str,
                  bins: int = 40, span: float = 0.30,
                  exclude_styles: frozenset[str] = EXCLUDED_DEFAULT) -> dict | None:
    """Liquidaciones pendientes de las posiciones seguidas, agrupadas por precio.

    Largos se liquidan por debajo del mark y cortos por encima. Se ignoran liq_px del lado
    equivocado (posición cambiada desde la última lectura) y fuera de ±span (se acumulan en `outside`).
    """
    mark = marks.get(coin)
    if not mark:
        return None
    lo, hi = mark * (1 - span), mark * (1 + span)
    step = (hi - lo) / bins
    buckets = [{"px_lo": lo + i * step, "px_hi": lo + (i + 1) * step, "long_usd": 0.0, "short_usd": 0.0}
               for i in range(bins)]
    tot_long = tot_short = out_long = out_short = 0.0
    near: list[dict] = []
    within = {pct: {"long_usd": 0.0, "short_usd": 0.0} for pct in (2, 5, 10)}
    for (addr, c), pos in book.positions.items():
        if c != coin or not pos.liq_px:
            continue
        info = wallets.get(addr)
        if info is None or info.style in exclude_styles:
            continue
        notional = abs(pos.szi) * mark
        is_long = pos.szi > 0
        if (is_long and pos.liq_px >= mark) or (not is_long and pos.liq_px <= mark):
            continue
        dist = abs(pos.liq_px - mark) / mark
        for pct, acc_ in within.items():
            if dist * 100 <= pct:
                acc_["long_usd" if is_long else "short_usd"] += notional
        near.append({"address": addr, "side": pos.side, "notional": notional, "liq_px": pos.liq_px,
                     "dist_pct": dist * 100, "lev": pos.leverage})
        if is_long:
            tot_long += notional
        else:
            tot_short += notional
        if not lo <= pos.liq_px < hi:
            if is_long:
                out_long += notional
            else:
                out_short += notional
            continue
        b = buckets[min(int((pos.liq_px - lo) / step), bins - 1)]
        b["long_usd" if is_long else "short_usd"] += notional
    near.sort(key=lambda x: x["dist_pct"])
    return {"coin": coin, "mark": mark, "bins": buckets, "total_long_liq": tot_long,
            "total_short_liq": tot_short, "outside_long": out_long, "outside_short": out_short,
            "within_pct": {str(k): v for k, v in within.items()}, "nearest": near[:6]}


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

    def series(self, coin: str, since: float = 0.0, max_points: int | None = None
               ) -> list[tuple[float, float]]:
        pts = [(ts, snap[coin][0]) for ts, snap in self._pts if coin in snap and ts >= since]
        if max_points and len(pts) > max_points:
            step = len(pts) / max_points
            pts = [pts[int(i * step)] for i in range(max_points)] + [pts[-1]]
        return pts

    # persistencia (para que los deltas sobrevivan a un reinicio)
    def dump(self) -> list:
        return [[ts, {c: list(v) for c, v in snap.items()}] for ts, snap in self._pts]

    def load(self, data: list, min_ts: float = 0.0) -> None:
        for ts, snap in data:
            if ts >= min_ts:
                self._pts.append((ts, {c: (v[0], v[1]) for c, v in snap.items()}))


def pinned_first(coins: Iterable[str], pinned: tuple[str, ...] = ("BTC", "ETH")) -> list[str]:
    cs = list(coins)
    return [c for c in pinned if c in cs] + [c for c in cs if c not in pinned]
