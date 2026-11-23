"""Validación histórica: ¿el sesgo de las ballenas predice retornos futuros?

    python -m whale_tracking.validate                 # 45 wallets, 90 días, BTC/ETH/SOL/HYPE
    python -m whale_tracking.validate --wallets 30 --days 60 --coins BTC ETH

Método (inspirado en hollowc2/Hyperliquid `basket_positioning.py`, MIT; ver THIRD_PARTY_NOTICES.md):
1. Misma selección de wallets que la app (leaderboard, direccionales, sin market makers).
2. Reconstruye la posición de cada wallet hora a hora desde sus fills (`startPosition`).
3. En cada hora calcula el sesgo de la cesta (Σ signo·nocional / Σ nocional) y la amplitud.
4. Lo compara con el retorno posterior a H horas usando muestras NO solapadas (cada H horas).
5. Mide: IC de Spearman + p-valor por permutación, tasa de acierto (binomial), Sharpe de seguir la señal
   frente a comprar y mantener, y estabilidad entre mitades del periodo.

Limitaciones que el informe repite: solo se recuperan ~10k fills por wallet, las wallets se eligen por su
rendimiento ACTUAL (sesgo de supervivencia: tiende a inflar el resultado), y las muestras son pocas.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

from .config import Settings
from .ranking import select_watchlist

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
WEIGHT_PER_MIN = 1000          # límite 1200/min por IP; margen de seguridad
INFO = "https://api.hyperliquid.xyz/info"


# ── API con límite de peso y caché en disco ────────────────────────────────────

class Api:
    def __init__(self, cache_dir: Path, ttl_s: int = 24 * 3600):
        self.http = httpx.Client(timeout=40)
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_s

    def post(self, payload: dict, weight: int):
        delay = 2.0
        for attempt in range(7):
            try:
                r = self.http.post(INFO, json=payload)
                if r.status_code == 200:
                    time.sleep(weight / WEIGHT_PER_MIN * 60)
                    return r.json()
                if r.status_code != 429 and r.status_code < 500:
                    r.raise_for_status()
            except httpx.HTTPError as e:
                if attempt == 6:
                    raise
                print(f"  reintento {payload['type']}: {e}", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, 60)
        raise RuntimeError("API no responde")

    def cached(self, name: str, fetch):
        path = self.dir / f"{name}.json"
        try:
            hit = json.loads(path.read_text())
            if time.time() - hit["t"] < self.ttl:
                return hit["d"]
        except (OSError, ValueError, KeyError):
            pass
        data = fetch()
        path.write_text(json.dumps({"t": time.time(), "d": data}))
        return data

    def fills(self, addr: str, since_ms: int, max_pages: int = 5) -> list[dict]:
        def fetch():
            out, seen, start = [], set(), since_ms
            for _ in range(max_pages):
                page = self.post({"type": "userFillsByTime", "user": addr, "startTime": start,
                                  "aggregateByTime": True}, 120)
                new = [f for f in page if (f["tid"], f["coin"]) not in seen]
                seen.update((f["tid"], f["coin"]) for f in new)
                out += new
                if len(page) < 2000 or not new:
                    break
                start = page[-1]["time"]
            return out
        return self.cached(f"fills_{addr}_{since_ms // DAY_MS}", fetch)

    def candles(self, coin: str, start_ms: int, end_ms: int) -> dict[int, float]:
        def fetch():
            out, t = [], start_ms
            while t < end_ms:
                page = self.post({"type": "candleSnapshot", "req": {
                    "coin": coin, "interval": "1h", "startTime": t, "endTime": end_ms}}, 20)
                if not page:
                    break
                out += page
                t = page[-1]["t"] + HOUR_MS
            return out
        data = self.cached(f"candles_{coin}_{start_ms // DAY_MS}", fetch)
        return {c["t"]: float(c["o"]) for c in data}


# ── Reconstrucción de posiciones ───────────────────────────────────────────────

def is_perp(f: dict) -> bool:
    return f["dir"] not in ("Buy", "Sell") and not f["coin"].startswith("@") and "/" not in f["coin"]


def position_paths(fills: list[dict]) -> dict[str, tuple[list[int], list[float]]]:
    """coin -> (tiempos, posición tras cada fill), con una semilla en el primer fill (startPosition)."""
    raw: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for f in sorted((f for f in fills if is_perp(f)), key=lambda f: f["time"]):
        sz = float(f["sz"]) if f["side"] == "B" else -float(f["sz"])
        if not raw[f["coin"]]:
            raw[f["coin"]].append((f["time"] - 1, float(f["startPosition"])))
        raw[f["coin"]].append((f["time"], round(float(f["startPosition"]) + sz, 10)))
    return {c: ([t for t, _ in p], [x for _, x in p]) for c, p in raw.items()}


def pos_at(path: tuple[list[int], list[float]], t: int) -> float | None:
    i = bisect.bisect_right(path[0], t) - 1
    return path[1][i] if i >= 0 else None


# ── Estadística ────────────────────────────────────────────────────────────────

def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 10 or len(set(x)) < 3 or len(set(y)) < 3:
        return None
    return statistics.correlation(x, y, method="ranked")


def perm_p(x: list[float], y: list[float], ic: float, n_perm: int = 3000, seed: int = 7) -> float:
    rng = random.Random(seed)
    yy = y[:]
    hits = 0
    for _ in range(n_perm):
        rng.shuffle(yy)
        c = spearman(x, yy)
        if c is not None and abs(c) >= abs(ic):
            hits += 1
    return (hits + 1) / (n_perm + 1)


def binom_p(k: int, n: int) -> float:
    """p-valor bilateral de un test de signos (p=0.5)."""
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def sharpe(rets: list[float], per_year: float) -> float | None:
    if len(rets) < 3:
        return None
    sd = statistics.stdev(rets)
    return None if sd == 0 else statistics.mean(rets) / sd * math.sqrt(per_year)


# ── Cesta histórica ────────────────────────────────────────────────────────────

def basket_series(paths_by_wallet: dict[str, tuple[int, dict]], coin: str,
                  prices: dict[int, float], hours: list[int], min_wallets: int) -> dict[int, dict]:
    """Por hora: sesgo (nocional) y amplitud entre wallets con posición conocida y no nula."""
    out: dict[int, dict] = {}
    for h in hours:
        px = prices.get(h)
        if px is None:
            continue
        longs = shorts = 0
        lu = su = 0.0
        live = 0
        for start, paths in paths_by_wallet.values():
            if start > h:
                continue
            live += 1
            p = paths.get(coin)
            pos = pos_at(p, h) if p else 0.0
            if pos is None:        # antes del primer fill de ese coin: posición = startPosition del primer fill
                pos = p[1][0]
            if pos > 0:
                longs += 1
                lu += pos * px
            elif pos < 0:
                shorts += 1
                su += -pos * px
        n = longs + shorts
        if live < min_wallets or n < 3:
            continue
        out[h] = {"bias": (lu - su) / (lu + su), "breadth": (longs - shorts) / n,
                  "n": n, "live": live, "net_usd": lu - su}
    return out


def evaluate(series: dict[int, dict], prices: dict[int, float], H: int, key: str = "bias",
             thr: float = 0.25, lo: int | None = None, hi: int | None = None, n_perm: int = 3000) -> dict | None:
    hs = sorted(h for h in series if (lo is None or h >= lo) and (hi is None or h < hi))
    if not hs:
        return None
    sig, fwd, follow, hold = [], [], [], []
    t = hs[0] + 24 * HOUR_MS          # deja 24 h de margen para calcular cambios
    while t + H * HOUR_MS <= hs[-1]:
        now, nxt = series.get(t), prices.get(t + H * HOUR_MS)
        prev = series.get(t - 24 * HOUR_MS)
        if now and nxt and prices.get(t):
            if key == "change":
                if prev:
                    sig.append(now["bias"] - prev["bias"])
                    fwd.append(nxt / prices[t] - 1)
            else:
                sig.append(now[key])
                fwd.append(nxt / prices[t] - 1)
        t += H * HOUR_MS
    n = len(sig)
    if n < 10:
        return {"n": n, "ic": None}
    ic = spearman(sig, fwd)
    pos = [(math.copysign(1, s) if abs(s) >= (thr if key != "change" else 0.1) else 0) for s in sig]
    follow = [p * r for p, r in zip(pos, fwd)]
    active = [(p, r) for p, r in zip(pos, fwd) if p]
    wins = sum(1 for p, r in active if p * r > 0)
    per_year = 8760 / H
    return {"n": n, "ic": ic, "p_ic": perm_p(sig, fwd, ic, n_perm) if ic is not None else None,
            "in_mkt": len(active) / n, "hit": wins / len(active) if active else None,
            "n_active": len(active), "p_hit": binom_p(wins, len(active)) if active else None,
            "sharpe_follow": sharpe(follow, per_year), "sharpe_hold": sharpe(fwd, per_year),
            "mean_follow_pct": 100 * statistics.mean(follow), "mean_hold_pct": 100 * statistics.mean(fwd)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wallets", type=int, default=45)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--coins", nargs="+", default=["BTC", "ETH", "SOL", "HYPE"])
    ap.add_argument("--horizons", nargs="+", type=int, default=[4, 24, 72])
    ap.add_argument("--min-wallets", type=int, default=8)
    ap.add_argument("--out", default="data/validation_report.json")
    args = ap.parse_args()

    cfg = Settings.from_env()
    api = Api(cfg.data_dir / "validate_cache")
    now_h = int(time.time() * 1000) // HOUR_MS * HOUR_MS
    since = now_h - args.days * DAY_MS

    print("leaderboard…", file=sys.stderr)
    rows = httpx.get(cfg.leaderboard_url, timeout=90).json()
    rows = rows.get("leaderboardRows", rows) if isinstance(rows, dict) else rows
    chosen = select_watchlist(rows, Settings(watch_size=args.wallets * 2, min_account=cfg.min_account,
                                             min_month_vlm=cfg.min_month_vlm))
    paths_by_wallet: dict[str, tuple[int, dict]] = {}
    for i, w in enumerate(chosen):
        if len(paths_by_wallet) >= args.wallets:
            break
        fills = api.fills(w.address, since)
        perp = [f for f in fills if is_perp(f)]
        print(f"  [{i + 1}/{len(chosen)}] {w.address[:10]} fills perp: {len(perp)}", file=sys.stderr)
        if len(perp) < 50:
            continue
        paths_by_wallet[w.address] = (min(f["time"] for f in perp), position_paths(perp))
    print(f"wallets usadas: {len(paths_by_wallet)}", file=sys.stderr)

    first = min(s for s, _ in paths_by_wallet.values()) // HOUR_MS * HOUR_MS
    first = max(first, since)
    hours = list(range(first, now_h + 1, HOUR_MS))
    report: dict = {"generated": time.time(), "wallets": len(paths_by_wallet), "days": args.days,
                    "from": first, "to": now_h, "results": []}
    for coin in args.coins:
        prices = api.candles(coin, first, now_h + args.horizons[-1] * HOUR_MS)
        series = basket_series(paths_by_wallet, coin, prices, hours, args.min_wallets)
        if not series:
            print(f"{coin}: sin datos suficientes", file=sys.stderr)
            continue
        mid = sorted(series)[len(series) // 2]
        for H in args.horizons:
            for key in ("bias", "change"):
                full = evaluate(series, prices, H, key)
                h1 = evaluate(series, prices, H, key, hi=mid, n_perm=1000)
                h2 = evaluate(series, prices, H, key, lo=mid, n_perm=1000)
                report["results"].append({"coin": coin, "H": H, "signal": key, "full": full,
                                          "first_half": h1, "second_half": h2,
                                          "avg_wallets": statistics.mean(v["n"] for v in series.values())})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=1))
    print_report(report)


def _f(v, spec):
    return "—" if v is None else format(v, spec)


def print_report(rep: dict) -> None:
    print(f"\nWallets: {rep['wallets']} · {rep['days']} días · muestras NO solapadas\n")
    print(f"{'coin':5}{'señal':>7}{'H':>4}{'n':>5}{'IC':>7}{'p(IC)':>7}{'acierto':>8}{'p':>6}"
          f"{'Sh seguir':>10}{'Sh B&H':>8} | IC 1ª/2ª mitad")
    for r in rep["results"]:
        f = r["full"]
        if not f or f.get("ic") is None:
            continue
        h1, h2 = r["first_half"] or {}, r["second_half"] or {}
        print(f"{r['coin']:5}{r['signal']:>7}{r['H']:>4}{f['n']:>5}{_f(f['ic'], '7.2f')}{_f(f['p_ic'], '7.3f')}"
              f"{_f(f['hit'], '8.0%')}{_f(f['p_hit'], '6.2f')}{_f(f['sharpe_follow'], '10.2f')}"
              f"{_f(f['sharpe_hold'], '8.2f')} | {_f(h1.get('ic'), '5.2f')} / {_f(h2.get('ic'), '5.2f')}")


if __name__ == "__main__":
    main()
