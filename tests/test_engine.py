import asyncio

from whale_tracking.config import Settings
from whale_tracking.engine import Engine
from whale_tracking.state import Position, WalletInfo


def make_engine(tmp_path):
    e = Engine(Settings(data_dir=tmp_path))
    e.wallets = {f"0x{i}": WalletInfo(f"0x{i}", style="directional") for i in range(6)}
    for a in e.wallets:
        e.book.positions[(a, "kPEPE")] = Position(a, "kPEPE", 1000, 1.0, 0, liq_px=0.5)
        e.book.positions[(a, "BTC")] = Position(a, "BTC", -1, 100, 0, liq_px=150)
    e.marks = {"kPEPE": 1.0, "BTC": 100.0}
    return e


def test_mixed_case_coins_do_not_break_signals(tmp_path):
    e = make_engine(tmp_path)
    names = [s["coin"] for s in e.signals(min_wallets=3)]
    assert set(names) == {"BTC", "kPEPE"}                 # antes: None -> TypeError que tumbaba el motor
    assert e.signal("kpepe")["coin"] == "kPEPE"            # sin distinguir mayúsculas
    assert e.liq_map("KPEPE")["coin"] == "kPEPE"
    a = e.alignment("kpepe", "long")
    assert a["coin"] == "kPEPE" and a["label"] == "neutral"
    assert e.alignment("NOPE", "long") is None
    asyncio.run(e.client.close())
