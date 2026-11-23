import math
import random

from whale_tracking.validate import (HOUR_MS, basket_series, binom_p, evaluate, is_perp, perm_p,
                                     position_paths, pos_at, spearman)


def f(t, coin, side, sz, start, d="Open Long"):
    return {"time": t, "coin": coin, "side": side, "sz": str(sz), "startPosition": str(start), "dir": d,
            "px": "100", "tid": t}


def test_is_perp_filters_spot():
    assert is_perp({"coin": "BTC", "dir": "Open Long"})
    assert not is_perp({"coin": "@107", "dir": "Buy"})
    assert not is_perp({"coin": "PURR/USDC", "dir": "Sell"})


def test_position_paths_and_pos_at():
    fills = [f(1000, "BTC", "B", 2, 0), f(2000, "BTC", "A", 5, 2, "Close Long"), f(3000, "ETH", "B", 1, 0)]
    p = position_paths(fills)
    assert pos_at(p["BTC"], 500) is None                 # antes del primer fill: desconocido
    assert pos_at(p["BTC"], 1500) == 2
    assert pos_at(p["BTC"], 2500) == -3                  # giro a corto
    assert pos_at(p["ETH"], 3000) == 1


def test_binom_and_spearman():
    assert math.isclose(binom_p(10, 10), 2 / 1024)
    assert binom_p(5, 10) == 1.0
    assert spearman([1, 2, 3], [1, 2, 3]) is None        # muy pocas muestras
    x = list(range(30))
    assert math.isclose(spearman(x, x), 1.0)
    assert perm_p(x, x, 1.0, n_perm=200) < 0.05


def _synthetic(predictive: bool, hours_n: int = 24 * 200, seed: int = 3):
    """Cesta sintética: 10 wallets cuya dirección (a) sí o (b) no anticipa el retorno de las siguientes 24 h."""
    rng = random.Random(seed)
    prices = {0: 100.0}
    rets = [rng.gauss(0, 0.004) for _ in range(hours_n + 100)]
    for i, r in enumerate(rets):
        prices[(i + 1) * HOUR_MS] = prices[i * HOUR_MS] * (1 + r)
    series = {}
    for i in range(hours_n):
        t = i * HOUR_MS
        fwd = prices[t + 24 * HOUR_MS] / prices[t] - 1
        base = math.copysign(0.6, fwd) if predictive else rng.choice([-0.6, 0.6])
        bias = max(-1, min(1, base + rng.gauss(0, 0.2)))
        series[t] = {"bias": bias, "breadth": bias, "n": 10, "live": 10, "net_usd": bias}
    return series, prices


def test_evaluate_detects_signal_and_noise():
    s, px = _synthetic(True)
    r = evaluate(s, px, 24, "bias", n_perm=500)
    assert r["ic"] > 0.3 and r["p_ic"] < 0.01 and r["hit"] > 0.7
    s, px = _synthetic(False)
    r = evaluate(s, px, 24, "bias", n_perm=500)
    assert abs(r["ic"]) < 0.3 and r["p_ic"] > 0.01


def test_basket_series_needs_min_wallets():
    paths = {f"0x{i}": (0, position_paths([f(1, "BTC", "B", 1, 0)])) for i in range(3)}
    prices = {2 * HOUR_MS: 100.0}
    assert basket_series(paths, "BTC", prices, [2 * HOUR_MS], min_wallets=8) == {}
    out = basket_series(paths, "BTC", prices, [2 * HOUR_MS], min_wallets=3)
    assert out[2 * HOUR_MS]["bias"] == 1.0 and out[2 * HOUR_MS]["n"] == 3
