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
