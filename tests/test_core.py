import math

from whale_tracking.aggregate import BiasHistory, compute_bias
from whale_tracking.ranking import grid_returns, score_from_sharpe, select_watchlist, sharpe
from whale_tracking.config import Settings
from whale_tracking.resolve import positions_from_clearinghouse, resolve_deltas
from whale_tracking.state import Book, Position, SeenTids, WalletInfo, apply_fill

A, B = "0xaaa", "0xbbb"


def fill(pos, delta, px, ts=1.0):
    return apply_fill(pos, address=A, coin="BTC", delta=delta, px=px, ts=ts)


def test_open_add_reduce_close_with_pnl():
    pos, ev = fill(None, 1.0, 100)
    assert ev[0].type == "open" and ev[0].side == "long"
    pos, ev = fill(pos, 1.0, 110)
    assert ev[0].type == "add" and math.isclose(pos.avg_entry, 105)
    pos, ev = fill(pos, -0.5, 120)
    assert ev[0].type == "reduce" and math.isclose(ev[0].realized_pnl, 7.5) and math.isclose(pos.szi, 1.5)
    pos, ev = fill(pos, -1.5, 90)
    assert pos is None and ev[0].type == "close" and math.isclose(ev[0].realized_pnl, -22.5)


def test_short_pnl_and_flip():
    pos, _ = fill(None, -2.0, 100)
    assert pos.side == "short"
    pos, ev = fill(pos, 3.0, 90)  # cierra 2 con ganancia (+20) y abre 1 largo
    assert [e.type for e in ev] == ["close", "open"]
    assert math.isclose(ev[0].realized_pnl, 20.0)
    assert pos.side == "long" and math.isclose(pos.szi, 1.0) and pos.avg_entry == 90


def test_resolve_deltas_buyer_seller():
    t = {"coin": "ETH", "px": "2000", "sz": "3", "time": 1700000000000, "users": [A.upper(), B]}
    fills = resolve_deltas(t, {A, B})
    assert {(f.address, f.delta) for f in fills} == {(A, 3.0), (B, -3.0)}
    assert resolve_deltas(t, {"0xother"}) == []
    assert resolve_deltas({"coin": "ETH", "users": [A]}, {A}) == []
    assert resolve_deltas({**t, "sz": "0"}, {A}) == []


def test_positions_from_clearinghouse():
    st = {"marginSummary": {"accountValue": "1000"}, "assetPositions": [
        {"position": {"coin": "BTC", "szi": "-0.5", "entryPx": "60000", "liquidationPx": "70000",
                      "leverage": {"value": 5}, "unrealizedPnl": "12.5"}},
        {"position": {"coin": "ETH", "szi": "0.0"}}]}
    acct, ps = positions_from_clearinghouse(A, st)
    assert acct == 1000 and len(ps) == 1 and ps[0].side == "short" and ps[0].leverage == 5


def test_book_reseed_epoch_guard():
    b = Book()
    ep = b.epoch(A)
    b.ingest(address=A, coin="BTC", delta=1, px=100, ts=1)   # llega un fill vivo
    assert b.reseed(A, [], expected_epoch=ep) is False        # snapshot viejo: no pisa
    assert (A, "BTC") in b.positions
    assert b.reseed(A, [], expected_epoch=None) is True
    assert not b.positions


def test_seen_tids():
    s = SeenTids(maxlen=10)
    assert s.check_and_add(1) is False and s.check_and_add(1) is True
    for i in range(100, 130):
        s.check_and_add(i)
    assert len(s._set) <= 10


def test_bias_weighted_and_label():
    b = Book()
    b.positions[(A, "BTC")] = Position(A, "BTC", 10, 100, 0)    # largo 10 BTC
    b.positions[(B, "BTC")] = Position(B, "BTC", -1, 100, 0)    # corto 1 BTC
    wallets = {A: WalletInfo(A, score=1.0), B: WalletInfo(B, score=1.0)}
    out = compute_bias(b, wallets, {"BTC": 100.0})["BTC"]
    assert out["long_usd"] == 1000 and out["short_usd"] == 100
    assert math.isclose(out["bias"], 900 / 1100) and out["label"] == "LARGO"
    assert out["breadth"] == 0.0
    # una wallet con poco peso casi no mueve el sesgo
    wallets[A].score = 0.2
    wallets[B].score = 1.0
    assert compute_bias(b, wallets, {"BTC": 100.0})["BTC"]["bias"] < out["bias"]


def test_bias_history_delta():
    h = BiasHistory()
    h.add(0, {"BTC": {"bias": 0.1, "net_usd": 10}})
    h.add(3600, {"BTC": {"bias": 0.4, "net_usd": 50}})
    d = h.delta("BTC", now=3600, seconds=3600)
    assert math.isclose(d["bias"], 0.3) and d["net_usd"] == 40
    assert h.delta("ETH", 3600, 3600) is None


def _series(n, step, pnl_step, av):
    return {"accountValueHistory": [[i * step, str(av + i * pnl_step)] for i in range(n)],
            "pnlHistory": [[i * step, str(i * pnl_step)] for i in range(n)]}


def test_returns_ignore_deposits():
    # PnL plano pero la cuenta crece por depósitos: retornos ~0
    s = {"accountValueHistory": [[i * 86_400_000, str(1000 + i * 100)] for i in range(30)],
         "pnlHistory": [[i * 86_400_000, "0"] for i in range(30)]}
    assert all(abs(r) < 1e-9 for r in grid_returns(s, 86_400_000))
    assert sharpe([0.0, 0.0, 0.0], 365) is None


def test_sharpe_and_score():
    s = sharpe([0.01, 0.02, 0.015, 0.012], 365)
    assert s and s > 0
    assert score_from_sharpe(None, None) == 0.5
    assert math.isclose(score_from_sharpe(3, 3), 1.0) and math.isclose(score_from_sharpe(-2, None), 0.2)


def test_select_watchlist_filters_and_orders():
    def row(a, acct, vlm, apnl):
        return {"ethAddress": a, "accountValue": str(acct), "windowPerformances": [
            ["month", {"pnl": "1", "roi": "0", "vlm": str(vlm)}],
            ["allTime", {"pnl": str(apnl), "roi": "0", "vlm": "0"}]]}
    rows = [row("0x1", 5e6, 1e8, 10), row("0x2", 9e6, 1e8, 10), row("0x3", 2e6, 1e3, 10),
            row("0x4", 3e6, 1e8, -5), row("0x5", 5e5, 1e8, 10)]
    cfg = Settings(watch_size=10, extra_wallets=("0xcustom",))
    wl = select_watchlist(rows, cfg)
    assert [w.address for w in wl] == ["0x2", "0x1", "0xcustom"] and wl[-1].custom


# ── v0.2: estilos, concentración, mapa de liquidaciones, histórico ─────────────

from whale_tracking.aggregate import build_liq_map
from whale_tracking.classify import classify


def test_classify_styles():
    w = WalletInfo(A, account_value=1e6, month_vlm=1e9)             # 1000x
    assert classify(w, [], {}) == "market_maker"
    w = WalletInfo(A, account_value=1e6, month_vlm=5e6)
    assert classify(w, [], {}) == "flat"
    assert classify(w, [Position(A, "BTC", 1, 100, 0)], {"BTC": 100}) == "directional"
    hedged = [Position(A, "BTC", 1, 100, 0), Position(A, "ETH", -1, 100, 0)]
    assert classify(w, hedged, {"BTC": 100, "ETH": 100}) == "hedged"


def test_market_makers_excluded_from_bias():
    b = Book()
    b.positions[(A, "BTC")] = Position(A, "BTC", 10, 100, 0)
    b.positions[(B, "BTC")] = Position(B, "BTC", -1, 100, 0)
    wallets = {A: WalletInfo(A, style="market_maker"), B: WalletInfo(B, style="directional")}
    out = compute_bias(b, wallets, {"BTC": 100.0})["BTC"]
    assert out["n_excluded"] == 1 and out["n_wallets"] == 1 and out["label"] == "CORTO"
    out_all = compute_bias(b, wallets, {"BTC": 100.0}, exclude_styles=frozenset())["BTC"]
    assert out_all["n_wallets"] == 2 and out_all["label"] == "LARGO"


def test_concentration_and_confidence():
    b = Book()
    wallets = {}
    for i in range(6):
        a = f"0x{i}"
        b.positions[(a, "BTC")] = Position(a, "BTC", 1 if i else 50, 100, 0)
        wallets[a] = WalletInfo(a, score=1.0)
    out = compute_bias(b, wallets, {"BTC": 100.0})["BTC"]
    assert out["concentrated"] and out["top1_share"] > 0.5
    assert 0 <= out["confidence"] <= 1
    # repartido entre más wallets => más confianza
    b2 = Book()
    w2 = {}
    for i in range(8):
        a = f"0x{i}"
        b2.positions[(a, "BTC")] = Position(a, "BTC", 5, 100, 0)
        w2[a] = WalletInfo(a, score=1.0)
    out2 = compute_bias(b2, w2, {"BTC": 100.0})["BTC"]
    assert not out2["concentrated"] and out2["confidence"] > out["confidence"]


def test_liq_map_sides_and_stale():
    b = Book()
    b.positions[(A, "BTC")] = Position(A, "BTC", 2, 100, 0, liq_px=80)       # largo: liq abajo
    b.positions[(B, "BTC")] = Position(B, "BTC", -1, 100, 0, liq_px=120)     # corto: liq arriba
    b.positions[("0xc", "BTC")] = Position("0xc", "BTC", 1, 100, 0, liq_px=150)  # largo con liq arriba: obsoleto
    b.positions[("0xd", "BTC")] = Position("0xd", "BTC", 3, 100, 0, liq_px=10)   # fuera de ±30 %
    wallets = {a: WalletInfo(a) for a in (A, B, "0xc", "0xd")}
    m = build_liq_map(b, wallets, {"BTC": 100.0}, "BTC")
    assert m["total_long_liq"] == 200 + 300 and m["total_short_liq"] == 100
    assert m["outside_long"] == 300
    assert sum(x["long_usd"] for x in m["bins"]) == 200 and sum(x["short_usd"] for x in m["bins"]) == 100
    assert m["nearest"][0]["dist_pct"] == 20.0
    assert build_liq_map(b, wallets, {}, "BTC") is None


def test_history_persistence_and_series():
    h = BiasHistory()
    for i in range(10):
        h.add(i * 60, {"BTC": {"bias": i / 10, "net_usd": i}})
    h2 = BiasHistory()
    h2.load(h.dump(), min_ts=60 * 3)
    assert len(h2.series("BTC")) == 7
    assert len(h.series("BTC", max_points=4)) == 5 and h.series("BTC", max_points=4)[-1][1] == 0.9


def test_select_watchlist_skips_market_makers():
    def row(a, acct, vlm):
        return {"ethAddress": a, "accountValue": str(acct), "windowPerformances": [
            ["month", {"pnl": "1", "roi": "0", "vlm": str(vlm)}], ["allTime", {"pnl": "9", "roi": "0", "vlm": "0"}]]}
    rows = [row("0xmm", 1e7, 1e10), row("0xdir", 5e6, 5e7)]       # 1000x vs 10x
    assert [w.address for w in select_watchlist(rows, Settings())] == ["0xdir"]
    assert len(select_watchlist(rows, Settings(include_mm=True))) == 2
