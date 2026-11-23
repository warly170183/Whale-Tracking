"""Backtest condicional: ¿las entradas a favor de la tendencia rinden mejor cuando las ballenas están alineadas?

    python -m whale_tracking.conditional                # reutiliza la caché de validate

Pregunta (distinta de validate.py, que miraba si el sesgo predice el precio): dado un trigger de tendencia
que NO usa datos de ballenas, ¿mejora el retorno direccional cuando el cambio del sesgo a 24 h acompaña?

Protocolo FIJADO antes de ver resultados (para no ajustarlo a los datos):
- Trigger de tendencia por horizonte H, sobre aperturas horarias: H=4 → EMA 6/24, H=24 → EMA 24/72,
  H=72 → EMA 72/240. Dirección = signo de (EMA rápida − lenta); se descarta si |diferencia| < 0.3 %.
- Un punto de decisión cada H horas (muestras no solapadas), por moneda; se mantiene H horas.
- Etiqueta de alineación: la MISMA función que usa la app en vivo (`alignment.classify_alignment`).
- Contraste principal: retorno direccional medio de «alineadas» vs «resto»; secundario: «alineadas» vs
  «en contra». Test de permutación unilateral con etiquetas barajadas DENTRO de cada moneda.
- Se reportan las tres horizontes sin elegir el mejor (3 contrastes principales: tenerlo en cuenta).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

from .aggregate import _confidence
from .alignment import classify_alignment
from .config import Settings
from .validate import DAY_MS, HOUR_MS, Api, basket_series, binom_p, load_basket

PAIRS = {4: (6, 24), 24: (24, 72), 72: (72, 240)}
MIN_TREND = 0.003


def ema(values: list[float], span: int) -> list[float]:
    k = 2 / (span + 1)
    out, cur = [], values[0]
    for v in values:
        cur = v * k + cur * (1 - k)
        out.append(cur)
    return out


def hourly_array(prices: dict[int, float], start: int, end: int) -> tuple[list[int], list[float]]:
    """Aperturas horarias continuas (rellena huecos con el último valor)."""
    hs = list(range(start, end + 1, HOUR_MS))
    out, last = [], None
    for h in hs:
        last = prices.get(h, last)
        out.append(last)
    first_ok = next((i for i, v in enumerate(out) if v is not None), None)
    if first_ok is None:
        return [], []
    return hs[first_ok:], out[first_ok:]


def trend_directions(prices: dict[int, float], start: int, end: int, fast: int, slow: int,
                     thr: float = MIN_TREND) -> dict[int, int]:
    hs, px = hourly_array(prices, start, end)
    if len(px) < slow * 2:
        return {}
    ef, es = ema(px, fast), ema(px, slow)
    out = {}
    for i, h in enumerate(hs):
        if i < slow:      # calentamiento
            continue
        diff = ef[i] / es[i] - 1
        if abs(diff) >= thr:
            out[h] = 1 if diff > 0 else -1
    return out


def build_samples(series: dict[int, dict], prices: dict[int, float], dirs: dict[int, int], H: int,
                  coin: str) -> list[dict]:
    hs = sorted(series)
    if not hs:
        return []
    samples = []
    t = hs[0] + 24 * HOUR_MS
    mid = hs[len(hs) // 2]
    while t + H * HOUR_MS <= hs[-1]:
        now, prev = series.get(t), series.get(t - 24 * HOUR_MS)
        p0, p1, d = prices.get(t), prices.get(t + H * HOUR_MS), dirs.get(t)
        if now and prev and p0 and p1 and d:
            conf = _confidence(now["n"], now["top1"], now["breadth"], now["bias"])
            al = classify_alignment("long" if d > 0 else "short", now["bias"] - prev["bias"], conf, now["n"])
            samples.append({"coin": coin, "H": H, "t": t, "dir": d, "label": al["label"],
                            "ret_pct": d * (p1 / p0 - 1) * 100, "half": 1 if t < mid else 2})
        t += H * HOUR_MS
    return samples


def _mean(xs: list[float]) -> float | None:
    return statistics.mean(xs) if xs else None


def summarize(samples: list[dict], n_perm: int = 5000, seed: int = 5) -> dict:
    groups = {}
    for lab in ("aligned", "neutral", "against"):
        rs = [s["ret_pct"] for s in samples if s["label"] == lab]
        wins = sum(r > 0 for r in rs)
        groups[lab] = {"n": len(rs), "mean_ret_pct": _mean(rs), "hit": wins / len(rs) if rs else None,
                       "p_hit": binom_p(wins, len(rs)) if rs else None}
    out = {"n": len(samples), "groups": groups, "tests": {}}

    def perm(group_a: set[str], group_b: set[str]) -> dict | None:
        sub = [s for s in samples if s["label"] in group_a | group_b]
        a = [s["ret_pct"] for s in sub if s["label"] in group_a]
        b = [s["ret_pct"] for s in sub if s["label"] in group_b]
        if len(a) < 8 or len(b) < 8:
            return None
        obs = statistics.mean(a) - statistics.mean(b)
        by_coin: dict[str, list[int]] = {}
        for i, s in enumerate(sub):
            by_coin.setdefault(s["coin"], []).append(i)
        labels = [s["label"] in group_a for s in sub]
        rets = [s["ret_pct"] for s in sub]
        rng, hits = random.Random(seed), 0
        for _ in range(n_perm):
            lab = labels[:]
            for idx in by_coin.values():
                vals = [lab[i] for i in idx]
                rng.shuffle(vals)
                for i, v in zip(idx, vals):
                    lab[i] = v
            ma = [r for r, l in zip(rets, lab) if l]
            mb = [r for r, l in zip(rets, lab) if not l]
            if statistics.mean(ma) - statistics.mean(mb) >= obs:
                hits += 1
        return {"diff_pct": obs, "n_a": len(a), "n_b": len(b), "p_one_sided": (hits + 1) / (n_perm + 1)}

    out["tests"]["aligned_vs_rest"] = perm({"aligned"}, {"neutral", "against"})
    out["tests"]["aligned_vs_against"] = perm({"aligned"}, {"against"})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wallets", type=int, default=45)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--coins", nargs="+", default=["BTC", "ETH", "SOL", "HYPE"])
    ap.add_argument("--min-wallets", type=int, default=8)
    ap.add_argument("--cache", default=None, help="directorio de caché de validate (por defecto data/validate_cache)")
    ap.add_argument("--out", default="data/conditional_report.json")
    args = ap.parse_args()

    cfg = Settings.from_env()
    api = Api(Path(args.cache) if args.cache else cfg.data_dir / "validate_cache")
    now_h = int(time.time() * 1000) // HOUR_MS * HOUR_MS
    since = now_h - args.days * DAY_MS
    paths, first, hours = load_basket(api, cfg, args.wallets, args.days, now_h, since)

    all_samples: dict[int, list[dict]] = {H: [] for H in PAIRS}
    warm = first - 300 * HOUR_MS
    for coin in args.coins:
        prices = api.candles(coin, warm, now_h + 72 * HOUR_MS)
        series = basket_series(paths, coin, prices, hours, args.min_wallets)
        for H, (fast, slow) in PAIRS.items():
            dirs = trend_directions(prices, warm, now_h, fast, slow)
            all_samples[H] += build_samples(series, prices, dirs, H, coin)

    report = {"generated": time.time(), "wallets": len(paths), "days": args.days, "results": {}}
    for H, samples in all_samples.items():
        report["results"][str(H)] = {"all": summarize(samples),
                                     "half1": summarize([s for s in samples if s["half"] == 1], 2000),
                                     "half2": summarize([s for s in samples if s["half"] == 2], 2000),
                                     "by_coin": {c: summarize([s for s in samples if s["coin"] == c], 1000)
                                                 for c in args.coins}}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=1))
    print_report(report)


def _f(v, spec):
    return "—" if v is None else format(v, spec)


def print_report(rep: dict) -> None:
    print(f"\nWallets {rep['wallets']} · {rep['days']} días · entradas a favor de tendencia, muestras no solapadas\n")
    print(f"{'H':>3} {'grupo':9}{'n':>5}{'ret medio %':>12}{'acierto':>9}{'p(acierto)':>11}")
    for H, r in rep["results"].items():
        for lab, g in r["all"]["groups"].items():
            print(f"{H:>3} {lab:9}{g['n']:>5}{_f(g['mean_ret_pct'], '12.2f')}{_f(g['hit'], '9.0%')}{_f(g['p_hit'], '11.2f')}")
        for name, t in r["all"]["tests"].items():
            if t:
                print(f"    {name}: dif {t['diff_pct']:+.2f}% (n {t['n_a']} vs {t['n_b']}), p unilateral {t['p_one_sided']:.3f}")
            else:
                print(f"    {name}: muestras insuficientes")
        h1 = r["half1"]["tests"].get("aligned_vs_rest")
        h2 = r["half2"]["tests"].get("aligned_vs_rest")
        print(f"    mitades (alineadas − resto): {_f(h1 and h1['diff_pct'], '+.2f')} / {_f(h2 and h2['diff_pct'], '+.2f')}")


if __name__ == "__main__":
    main()
