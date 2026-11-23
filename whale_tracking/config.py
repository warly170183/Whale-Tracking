"""Configuración por variables de entorno (prefijo WT_)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"WT_{name}", default)


def _csv(name: str) -> tuple[str, ...]:
    raw = _env(name, "")
    return tuple(x.strip() for x in raw.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    info_url: str = "https://api.hyperliquid.xyz/info"
    ws_url: str = "wss://api.hyperliquid.xyz/ws"
    leaderboard_url: str = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

    # Selección de ballenas
    watch_size: int = 100             # nº de wallets a seguir
    min_account: float = 1_000_000    # valor mínimo de cuenta (USD)
    min_month_vlm: float = 10_000_000 # volumen mínimo a 30d (USD)
    extra_wallets: tuple[str, ...] = ()  # wallets propias a añadir siempre
    coins: tuple[str, ...] = ()       # vacío = todos los perps

    # Tiempos
    reconcile_s: int = 300            # resincroniza posiciones con la cadena
    history_s: int = 60               # intervalo de la serie histórica del sesgo
    sharpe_enabled: bool = True
    sharpe_req_interval_s: float = 1.1  # el endpoint portfolio pesa 20 (límite 1200/min)
    portfolio_cache_ttl_s: int = 24 * 3600

    # Señales
    event_min_usd: float = 100_000    # eventos menores no salen en el feed
    bias_threshold: float = 0.25      # |sesgo| >= umbral => LARGO/CORTO
    mm_turnover: float = 60.0         # volumen 30d / cuenta >= X => market maker (excluido del sesgo)
    include_mm: bool = False          # True = los market makers también cuentan en el sesgo

    data_dir: Path = Path("data")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            watch_size=int(_env("WATCH_SIZE", "100")),
            min_account=float(_env("MIN_ACCOUNT", "1000000")),
            min_month_vlm=float(_env("MIN_MONTH_VLM", "10000000")),
            extra_wallets=tuple(w.lower() for w in _csv("EXTRA_WALLETS")),
            coins=_csv("COINS"),
            reconcile_s=int(_env("RECONCILE_S", "300")),
            history_s=int(_env("HISTORY_S", "60")),
            sharpe_enabled=_env("SHARPE", "1") != "0",
            event_min_usd=float(_env("EVENT_MIN_USD", "100000")),
            bias_threshold=float(_env("BIAS_THRESHOLD", "0.25")),
            mm_turnover=float(_env("MM_TURNOVER", "60")),
            include_mm=_env("INCLUDE_MM", "0") == "1",
            data_dir=Path(_env("DATA_DIR", "data")),
        )
