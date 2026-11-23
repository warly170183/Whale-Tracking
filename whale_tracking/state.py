"""Máquina de estados de posiciones: pliega fills firmados en eventos.

Inspirado en el diseño del libro de posiciones de KonScanner/hyperliquid-trader-tracker (MIT),
reescrito aquí con floats y sin dependencias.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EPS = 1e-12


@dataclass
class Position:
    address: str
    coin: str
    szi: float                      # tamaño firmado: >0 largo, <0 corto
    avg_entry: float
    opened_at: float                # epoch s
    leverage: int | None = None
    liq_px: float | None = None
    upnl: float | None = None

    @property
    def side(self) -> str:
        return "long" if self.szi > 0 else "short"


@dataclass
class Event:
    type: str                       # open | add | reduce | close
    address: str
    coin: str
    side: str                       # lado de la posición afectada
    size: float
    px: float
    notional: float
    ts: float
    new_szi: float
    realized_pnl: float | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _pnl(side: str, entry: float, exit_px: float, size: float) -> float:
    return (exit_px - entry) * size if side == "long" else (entry - exit_px) * size


def apply_fill(
    pos: Position | None, *, address: str, coin: str, delta: float, px: float, ts: float
) -> tuple[Position | None, list[Event]]:
    """Aplica un fill firmado (+compra, -venta). Devuelve (nueva posición | None, eventos)."""
    if abs(delta) < EPS:
        return pos, []

    if pos is None or abs(pos.szi) < EPS:
        new = Position(address, coin, delta, px, ts)
        ev = Event("open", address, coin, new.side, abs(delta), px, abs(delta) * px, ts, delta)
        return new, [ev]

    same_dir = (pos.szi > 0) == (delta > 0)
    if same_dir:
        total = abs(pos.szi) + abs(delta)
        pos.avg_entry = (pos.avg_entry * abs(pos.szi) + px * abs(delta)) / total
        pos.szi += delta
        ev = Event("add", address, coin, pos.side, abs(delta), px, abs(delta) * px, ts, pos.szi)
        return pos, [ev]

    old_side = pos.side
    closing = min(abs(delta), abs(pos.szi))
    pnl = _pnl(old_side, pos.avg_entry, px, closing)
    remaining = pos.szi + delta

    if abs(remaining) < EPS:  # cierre total
        ev = Event("close", address, coin, old_side, closing, px, closing * px, ts, 0.0, pnl)
        return None, [ev]

    if (remaining > 0) == (pos.szi > 0):  # reducción parcial
        pos.szi = remaining
        ev = Event("reduce", address, coin, old_side, closing, px, closing * px, ts, remaining, pnl)
        return pos, [ev]

    # giro (flip): cierra la pierna vieja y abre la contraria con el resto
    close_ev = Event("close", address, coin, old_side, closing, px, closing * px, ts, 0.0, pnl)
    new = Position(address, coin, remaining, px, ts)
    open_ev = Event("open", address, coin, new.side, abs(remaining), px, abs(remaining) * px, ts, remaining)
    return new, [close_ev, open_ev]


@dataclass
class WalletInfo:
    address: str
    account_value: float = 0.0       # equity perp (clearinghouseState), se refresca al sembrar
    lb_account: float = 0.0          # valor de cuenta del leaderboard (incluye más que el perp)
    month_pnl: float = 0.0
    alltime_pnl: float = 0.0
    month_vlm: float = 0.0
    sharpe_30d: float | None = None
    sharpe_all: float | None = None
    mdd_30d: float | None = None
    score: float = 0.5              # peso de la wallet en el sesgo agregado (0.2–1.0)
    custom: bool = False
    seeded: bool = False
    style: str = "unknown"          # market_maker | directional | hedged | flat | unknown


class Book:
    """Posiciones por (wallet, coin), con guarda de época para reconciliar sin pisar fills vivos."""

    def __init__(self) -> None:
        self.positions: dict[tuple[str, str], Position] = {}
        self._epoch: dict[str, int] = {}

    def ingest(self, *, address: str, coin: str, delta: float, px: float, ts: float) -> list[Event]:
        key = (address, coin)
        new, events = apply_fill(self.positions.get(key), address=address, coin=coin,
                                 delta=delta, px=px, ts=ts)
        if new is None:
            self.positions.pop(key, None)
        else:
            self.positions[key] = new
        self._epoch[address] = self._epoch.get(address, 0) + 1
        return events

    def epoch(self, address: str) -> int:
        return self._epoch.get(address, 0)

    def reseed(self, address: str, positions: list[Position], expected_epoch: int | None = None) -> bool:
        """Reemplaza todas las posiciones de la wallet. Si llegó un fill vivo entre medias, no aplica."""
        if expected_epoch is not None and self._epoch.get(address, 0) != expected_epoch:
            return False
        for key in [k for k in self.positions if k[0] == address]:
            del self.positions[key]
        for p in positions:
            self.positions[(address, p.coin)] = p
        return True

    def drop(self, address: str) -> None:
        for key in [k for k in self.positions if k[0] == address]:
            del self.positions[key]
        self._epoch.pop(address, None)

    def of_wallet(self, address: str) -> list[Position]:
        return [p for (a, _), p in self.positions.items() if a == address]


@dataclass
class SeenTids:
    """Conjunto FIFO acotado de trade ids (idempotencia ante reconexiones del WebSocket)."""

    maxlen: int = 50_000
    _order: list = field(default_factory=list)
    _set: set = field(default_factory=set)

    def check_and_add(self, tid: int) -> bool:
        if tid in self._set:
            return True
        self._set.add(tid)
        self._order.append(tid)
        if len(self._order) > self.maxlen:
            drop = self._order[: self.maxlen // 10]
            del self._order[: self.maxlen // 10]
            self._set.difference_update(drop)
        return False
