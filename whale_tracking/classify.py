"""Clasificación de estilo de wallet.

Los market makers mueven mucho volumen con poco capital y su posición neta es inventario, no
convicción: contarlos como "ballenas direccionales" distorsiona el sesgo. Criterio (heurístico):

- market_maker: volumen 30d / valor de cuenta >= mm_turnover (por defecto 60x; en el top-100 del
  leaderboard la mediana es ~7x y ~20 % supera 60x)
- flat: sin posiciones abiertas
- directional: |neto| / bruto >= 0.5
- hedged: el resto
"""

from __future__ import annotations

from .state import Position, WalletInfo


def classify(info: WalletInfo, positions: list[Position], marks: dict[str, float],
             mm_turnover: float = 60.0) -> str:
    # Se usa el valor de cuenta del leaderboard: contra la equity perp (mucho menor en muchas wallets)
    # un trader direccional apalancado parecería un market maker.
    base = info.lb_account or info.account_value
    turnover = info.month_vlm / base if base > 0 else 0.0
    if turnover >= mm_turnover:
        return "market_maker"
    gross = net = 0.0
    for p in positions:
        n = p.szi * (marks.get(p.coin) or p.avg_entry)
        gross += abs(n)
        net += n
    if gross == 0:
        return "flat"
    return "directional" if abs(net) / gross >= 0.5 else "hedged"
