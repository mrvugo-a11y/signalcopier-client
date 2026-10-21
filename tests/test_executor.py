"""Executor logic tested against a fake MT5 module (no terminal needed)."""
import os, sys, types, unittest
from collections import namedtuple
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier import executor
from copier.parser import parse

Info = namedtuple("Info", "name point digits volume_step volume_min volume_max trade_tick_size trade_tick_value filling_mode")
Tick = namedtuple("Tick", "bid ask")
Res = namedtuple("Res", "retcode order deal comment")

def fake_mt5():
    m = types.SimpleNamespace()
    for i, n in enumerate("ORDER_TYPE_BUY ORDER_TYPE_SELL ORDER_TYPE_BUY_LIMIT ORDER_TYPE_SELL_LIMIT ORDER_TYPE_BUY_STOP ORDER_TYPE_SELL_STOP".split()):
        setattr(m, n, i)
    m.TRADE_ACTION_DEAL, m.TRADE_ACTION_PENDING = 1, 5
    m.ORDER_FILLING_FOK = m.ORDER_FILLING_IOC = m.ORDER_FILLING_RETURN = 0
    m.ORDER_TIME_GTC = 0; m.TRADE_RETCODE_DONE, m.TRADE_RETCODE_PLACED, m.TRADE_RETCODE_DONE_PARTIAL = 10009, 10008, 10010
    syms = {"XAUUSDm": Info("XAUUSDm", 0.01, 2, 0.01, 0.01, 100, 0.01, 0.01, 1)}
    m.symbol_info = lambda s: syms.get(s)
    m.symbols_get = lambda: list(syms.values())
    m.symbol_select = lambda s, b: True
    m.symbol_info_tick = lambda s: Tick(1950.00, 1950.20)
    m.account_info = lambda: types.SimpleNamespace(balance=10000)
    m.order_calc_profit = lambda t, s, v, a, b: -(abs(a - b) * 100 * v)
    m.positions_get = lambda **k: []; m.orders_get = lambda **k: []
    m.sent = []
    def send(r): m.sent.append(r); return Res(10009, 1000 + len(m.sent), 0, "ok")
    m.order_send = send; m.last_error = lambda: None
    return m

ST = {"lot_mode": "risk", "risk_percent": 1, "split_tps": True, "use_tps": [1, 2, 3, 4, 5],
      "max_spread_points": 50, "magic_number": 7, "symbol_map": {"GOLD": "XAUUSD"}, "pending_orders": True}

class ExecTests(unittest.TestCase):
    def setUp(self):
        self.m = executor.mt5 = fake_mt5()
        self.ex = executor.Executor({}, lambda s: None)

    def test_split_risk_suffix(self):
        t = self.ex.open_signal(parse("GOLD BUY 1950 SL 1940 TP1 1955 TP2 1960"), ST)
        self.assertEqual(len(t), 2)
        # market ask 1950.20, SL 1940 -> 10.20 risk * 100/lot; 1% of 10000 = 100 USD -> 0.098 total -> total rounded DOWN to 0.09, spread 0.05 + 0.04
        self.assertEqual([r["volume"] for r in self.m.sent], [0.05, 0.04])
        self.assertEqual(self.m.sent[0]["symbol"], "XAUUSDm")
        self.assertEqual([r["tp"] for r in self.m.sent], [1955, 1960])

    def test_spread_filter(self):
        self.assertEqual(self.ex.open_signal(parse("XAUUSD BUY SL 1940 TP 1960"), dict(ST, max_spread_points=10)), [])

    def test_buy_limit(self):
        self.ex.open_signal(parse("XAUUSD BUY LIMIT 1940 SL 1930 TP 1960"), dict(ST, lot_mode="fixed", fixed_lot=0.02))
        r = self.m.sent[0]
        self.assertEqual((r["type"], r["price"], r["volume"], r["action"]), (self.m.ORDER_TYPE_BUY_LIMIT, 1940, 0.02, 5))

    def test_range_outside_becomes_limit(self):
        self.ex.open_signal(parse("XAUUSD SELL 1960-1965 SL 1975 TP 1940"), dict(ST, range_entry="first"))
        self.assertEqual((self.m.sent[0]["type"], self.m.sent[0]["price"]), (self.m.ORDER_TYPE_SELL_LIMIT, 1960))

if __name__ == "__main__":
    unittest.main(verbosity=2)
