import random

from whale_tracking.alignment import classify_alignment
from whale_tracking.conditional import build_samples, ema, summarize, trend_directions
from whale_tracking.journal import Journal
from whale_tracking.validate import HOUR_MS


def test_alignment_rules():
    c = classify_alignment
    assert c("long", 0.2, 0.8, 10)["label"] == "aligned"
    assert c("short", 0.2, 0.8, 10)["label"] == "against"
    assert c("short", -0.2, 0.8, 10)["label"] == "aligned"
    assert c("long", 0.02, 0.8, 10)["label"] == "neutral"       # cambio pequeño
    assert c("long", 0.2, 0.5, 10)["label"] == "neutral"        # confianza baja
    assert c("long", 0.2, 0.8, 3)["label"] == "neutral"         # pocas wallets
    assert c("long", None, 0.8, 10)["label"] == "neutral"       # sin histórico


def test_ema_and_trend_dirs():
    assert ema([1, 1, 1], 3) == [1, 1, 1]
    up = {i * HOUR_MS: 100 * (1.004 ** i) for i in range(400)}
    d = trend_directions(up, 0, 399 * HOUR_MS, 6, 24)
    assert d and set(d.values()) == {1}
    flat = {i * HOUR_MS: 100.0 for i in range(400)}
    assert trend_directions(flat, 0, 399 * HOUR_MS, 6, 24) == {}


def _fake(effect: float, seed: int = 1, n_per_coin: int = 150):
    """Muestras sintéticas: si effect>0, las alineadas rinden más."""
    rng, out = random.Random(seed), []
    for coin in ("BTC", "ETH", "SOL"):
        for i in range(n_per_coin):
            lab = rng.choice(["aligned", "neutral", "against"])
            base = rng.gauss(0, 1.0) + (effect if lab == "aligned" else 0)
            out.append({"coin": coin, "label": lab, "ret_pct": base, "half": 1 + i % 2})
    return out


def test_summarize_detects_effect_and_null():
    r = summarize(_fake(0.8), n_perm=500)
    assert r["tests"]["aligned_vs_rest"]["p_one_sided"] < 0.01
    r0 = summarize(_fake(0.0), n_perm=500)
    assert r0["tests"]["aligned_vs_rest"]["p_one_sided"] > 0.05
    assert summarize(_fake(0.0)[:10])["tests"]["aligned_vs_rest"] is None   # muestras insuficientes


def test_build_samples_uses_live_alignment():
    H = 24
    series = {i * HOUR_MS: {"bias": (i / 200), "breadth": 0.5, "n": 10, "live": 10, "net_usd": 1, "top1": 0.1}
              for i in range(200)}
    prices = {i * HOUR_MS: 100 + i for i in range(300)}
    dirs = {i * HOUR_MS: 1 for i in range(300)}
    s = build_samples(series, prices, dirs, H, "BTC")
    assert s and all(x["label"] in ("aligned", "neutral", "against") for x in s)
    # el sesgo sube 0.12 cada 24 h con confianza alta => alineadas para un largo
    assert any(x["label"] == "aligned" for x in s)
    assert all(x["ret_pct"] > 0 for x in s)          # el precio sube siempre


def test_journal_roundtrip_and_stats(tmp_path):
    j = Journal(tmp_path / "j.json")
    rng = random.Random(2)
    for i in range(30):
        lab = "aligned" if i % 2 else "neutral"
        e = j.add("BTC", "long", 100.0, {"label": lab, "reasons": []}, None, risk_pct=1.0)
        j.close(e["id"], 100.0 * (1 + (0.02 if lab == "aligned" else -0.01) + rng.gauss(0, 0.002)))
    st = j.stats(n_perm=500)
    assert st["closed"] == 30 and st["groups"]["aligned"]["n"] == 15
    assert st["groups"]["aligned"]["mean_r"] > 1.0
    assert st["test"]["p_one_sided"] < 0.01
    assert Journal(tmp_path / "j.json").entries[0]["id"] == j.entries[0]["id"]     # persistencia
    assert j.close("nope", 1.0) is None
    assert Journal(tmp_path / "x.json").stats()["test"].startswith("insuficiente")
