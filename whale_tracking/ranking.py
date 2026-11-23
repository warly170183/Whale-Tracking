"""Selección de ballenas desde el leaderboard y puntuación por Sharpe.

El cálculo de retornos por PnL (descontando depósitos/retiros), Sharpe y drawdown está adaptado
de hollowc2/Hyperliquid, wallet_tracker/rank_sharpe.py (MIT). Ver THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import bisect
import math
import statistics
from typing import Any

from .config import Settings
from .state import WalletInfo

DAY_MS = 86_400_000
WEEK_MS = 7 * DAY_MS
MIN_DAYS = 20
MIN_WEEKS = 26


# ── Selección ────────────────────────────────────────────────────────────────

def _window(row: dict, name: str) -> dict:
    for k, v in row.get("windowPerformances", []):
        if k == name:
            return v
    return {}


def select_watchlist(rows: list[dict], cfg: Settings) -> list[WalletInfo]:
    """Top N por valor de cuenta entre las que tienen volumen reciente y PnL histórico positivo."""
    cands: list[WalletInfo] = []
    for r in rows:
        addr = str(r.get("ethAddress", "")).lower()
        if not addr:
            continue
        try:
            acct = float(r.get("accountValue", 0))
            m = _window(r, "month")
            a = _window(r, "allTime")
            vlm = float(m.get("vlm", 0))
            apnl = float(a.get("pnl", 0))
        except (TypeError, ValueError):
            continue
        if acct < cfg.min_account or vlm < cfg.min_month_vlm or apnl <= 0:
            continue
        cands.append(WalletInfo(address=addr, account_value=acct, month_pnl=float(m.get("pnl", 0)),
                                alltime_pnl=apnl, month_vlm=vlm))
    cands.sort(key=lambda w: w.account_value, reverse=True)
    chosen = cands[: cfg.watch_size]
    have = {w.address for w in chosen}
    for addr in cfg.extra_wallets:
        if addr not in have:
            chosen.append(WalletInfo(address=addr, custom=True))
    return chosen


# ── Métricas (adaptadas de rank_sharpe.py, MIT) ──────────────────────────────

def grid_returns(series: dict, step_ms: int) -> list[float]:
    """Remuestrea una ventana de portfolio y devuelve dPnL / capital previo (ignora depósitos)."""
    av = series.get("accountValueHistory") or []
    pnl = series.get("pnlHistory") or []
    if len(av) < 3 or len(av) != len(pnl):
        return []
    ts = [p[0] for p in av]
    avs = [float(p[1]) for p in av]
    pnls = [float(p[1]) for p in pnl]
    floor = 0.1 * statistics.median(avs)

    def at(t: float) -> tuple[float, float]:
        i = max(bisect.bisect_right(ts, t) - 1, 0)
        return avs[i], pnls[i]

    rets: list[float] = []
    t = ts[0]
    prev_av, prev_pnl = at(t)
    while t + step_ms <= ts[-1]:
        t += step_ms
        cur_av, cur_pnl = at(t)
        dpnl = cur_pnl - prev_pnl
        base = max(prev_av, cur_av - dpnl)
        if base > 0 and base >= floor:
            rets.append(dpnl / base)
        prev_av, prev_pnl = cur_av, cur_pnl
    return rets


def sharpe(rets: list[float], periods_per_year: int) -> float | None:
    if len(rets) < 2:
        return None
    sd = statistics.stdev(rets)
    if sd == 0:
        return None
    return statistics.mean(rets) / sd * math.sqrt(periods_per_year)


def max_drawdown(rets: list[float]) -> float:
    eq = peak = 1.0
    mdd = 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    return mdd


def score_from_sharpe(s30: float | None, sall: float | None) -> float:
    """Peso 0.2–1.0. Sin datos suficientes => 0.5 (neutro). Sharpe >= 3 => 1.0."""
    vals = [v for v in (s30, sall) if v is not None]
    if not vals:
        return 0.5
    s = sum(vals) / len(vals)
    return 0.2 + 0.8 * min(max(s, 0.0), 3.0) / 3.0


def apply_portfolio(info: WalletInfo, portfolio: Any) -> None:
    """Rellena Sharpe/drawdown/score de la wallet a partir de la respuesta `portfolio`."""
    windows = dict(portfolio)
    d = grid_returns(windows["perpMonth"], DAY_MS) if "perpMonth" in windows else []
    w = grid_returns(windows["perpAllTime"], WEEK_MS) if "perpAllTime" in windows else []
    info.sharpe_30d = sharpe(d, 365) if len(d) >= MIN_DAYS else None
    info.sharpe_all = sharpe(w, 52) if len(w) >= MIN_WEEKS else None
    info.mdd_30d = max_drawdown(d) if d else None
    info.score = score_from_sharpe(info.sharpe_30d, info.sharpe_all)
