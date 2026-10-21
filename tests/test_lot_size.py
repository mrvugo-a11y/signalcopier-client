"""Lot size bug: every trade opened at 0.01 lots. Exness MT5 real, account currency ZAR, XAUUSDm (100 oz)."""
import json, os, sys, tempfile, types, unittest
from collections import namedtuple
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier import executor, config as C
from copier.parser import parse

Info = namedtuple("Info", "name point digits volume_step volume_min volume_max trade_tick_size trade_tick_value filling_mode")
Tick = namedtuple("Tick", "bid ask")
Res = namedtuple("Res", "retcode order deal comment")
USDZAR = 18.0
# Exness XAUUSDm: 3 digits, 100 oz contract, tick 0.001 -> 0.1 USD -> 1.8 ZAR per lot
XAU = Info("XAUUSDm", 0.001, 3, 0.01, 0.01, 200, 0.001, 0.001 * 100 * USDZAR, 1)
EUR = Info("EURUSDm", 0.00001, 5, 0.01, 0.01, 200, 0.00001, 0.00001 * 100000 * USDZAR, 1)


def fake(balance, calc=True):
    m = types.SimpleNamespace()
    for i, n in enumerate("ORDER_TYPE_BUY ORDER_TYPE_SELL ORDER_TYPE_BUY_LIMIT ORDER_TYPE_SELL_LIMIT ORDER_TYPE_BUY_STOP ORDER_TYPE_SELL_STOP".split()):
        setattr(m, n, i)
    m.TRADE_ACTION_DEAL, m.TRADE_ACTION_PENDING = 1, 5
    m.ORDER_FILLING_FOK = m.ORDER_FILLING_IOC = m.ORDER_FILLING_RETURN = 0
    m.ORDER_TIME_GTC = 0; m.TRADE_RETCODE_DONE, m.TRADE_RETCODE_PLACED, m.TRADE_RETCODE_DONE_PARTIAL = 10009, 10008, 10010
    syms = {"XAUUSDm": XAU, "EURUSDm": EUR}
    m.symbol_info = lambda s: syms.get(s); m.symbols_get = lambda: list(syms.values()); m.symbol_select = lambda s, b: True
    m.symbol_info_tick = lambda s: Tick(1950.000, 1950.200) if s == "XAUUSDm" else Tick(1.08500, 1.08510)
    m.account_info = lambda: types.SimpleNamespace(balance=balance, currency="ZAR")
    size = {"XAUUSDm": 100, "EURUSDm": 100000}
    m.order_calc_profit = (lambda t, s, v, a, b: -abs(a - b) * size[s] * v * USDZAR) if calc else (lambda *a: None)
    m.positions_get = lambda **k: []; m.orders_get = lambda **k: []
    m.sent = []
    def send(r): m.sent.append(r); return Res(10009, 1000 + len(m.sent), 0, "ok")
    m.order_send = send; m.last_error = lambda: None
    return m


ST = dict(C.DEFAULT_SETTINGS, max_spread_points=0, symbol_suffix="")


class ZarLotTests(unittest.TestCase):
    def run_sig(self, text, st, balance=200000, calc=True):
        self.m = executor.mt5 = fake(balance, calc); self.logs = []
        executor.Executor({}, self.logs.append).open_signal(parse(text), st)
        return [r["volume"] for r in self.m.sent]

    def test_fixed_lot_used(self):
        self.assertEqual(self.run_sig("XAUUSD BUY 1950 SL 1940 TP1 1955 TP2 1960 TP3 1970", dict(ST, fixed_lot=0.05)), [0.05] * 3)

    def test_fixed_lot_odd_values(self):
        for v in (0.03, 0.07, 0.29, 0.1, 1.15):
            self.assertEqual(self.run_sig("EURUSD BUY SL 1.0800 TP 1.0900", dict(ST, fixed_lot=v)), [v])

    def test_risk_zar_xau(self):
        # 1% of R200 000 = R2 000; 10.2 USD * 100 oz * 18 = R18 360 per lot -> 0.1089 -> 0.10 total over 2 trades
        v = self.run_sig("GOLD BUY 1950 SL 1940 TP1 1955 TP2 1960", dict(ST, lot_mode="risk", risk_percent=1))
        self.assertEqual(v, [0.05, 0.05])

    def test_risk_split_uneven_not_all_min(self):
        # 2.5% of R100 000 = R2 500 / R18 360 = 0.136 -> 0.13 over 3 trades -> 0.05, 0.04, 0.04 (old code: 0.04 x3)
        v = self.run_sig("GOLD BUY 1950 SL 1940 TP1 1955 TP2 1960 TP3 1970", dict(ST, lot_mode="risk", risk_percent=2.5), 100000)
        self.assertEqual(v, [0.05, 0.04, 0.04])
        # 0.05 total over 3 trades: old code gave 0.01 x3, now 0.02, 0.02, 0.01
        v = self.run_sig("GOLD BUY 1950 SL 1940 TP1 1955 TP2 1960 TP3 1970", dict(ST, lot_mode="risk", risk_percent=1), 92000)
        self.assertEqual(v, [0.02, 0.02, 0.01])

    def test_risk_tick_value_fallback_in_zar(self):
        v = self.run_sig("GOLD BUY 1950 SL 1940 TP1 1955 TP2 1960", dict(ST, lot_mode="risk", risk_percent=1), calc=False)
        self.assertEqual(v, [0.05, 0.05])

    def test_risk_eurusd_zar(self):
        # 1% of R200 000 = R2 000; SL 50 pips = 500 USD/lot = R9 000 -> 0.222 -> 0.22
        self.assertEqual(self.run_sig("EURUSD SELL SL 1.0900 TP 1.0800", dict(ST, lot_mode="risk", risk_percent=1)), [0.22])

    def test_small_zar_balance_explains_min_lot(self):
        v = self.run_sig("GOLD BUY 1950 SL 1940 TP 1960", dict(ST, lot_mode="risk", risk_percent=1), 10000)
        self.assertEqual(v, [0.01])
        self.assertTrue(any("smallest lot" in l and "ZAR" in l for l in self.logs), self.logs)

    def test_risk_without_sl_says_fixed_is_used(self):
        v = self.run_sig("GOLD BUY TP 1960", dict(ST, lot_mode="risk", fixed_lot=0.03))
        self.assertEqual(v, [0.03])
        self.assertTrue(any("no stop loss" in l for l in self.logs))

    def test_risk_without_sl_uses_default_sl_points(self):
        # default 10000 points = 10.00 on XAUUSDm (3 digits)
        v = self.run_sig("GOLD BUY TP 1960", dict(ST, lot_mode="risk", risk_percent=1, default_sl_points=10000))
        self.assertEqual(v, [0.11])


class SavedSettingsTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); os.environ["SIGNALPILOT_HOME"] = self.d

    def tearDown(self):
        os.environ.pop("SIGNALPILOT_HOME", None)

    def test_saved_values_survive_restart_and_reach_executor(self):
        cfg = C.load(); cfg["default_settings"].update(lot_mode="risk", risk_percent=2.0, fixed_lot=0.05)
        cfg["channels"] = {"-100": {"name": "Gold", "enabled": True, "settings": {}}}
        C.save(cfg)
        st = C.channel_settings(C.load(), -100)
        self.assertEqual((st["lot_mode"], st["risk_percent"], st["fixed_lot"]), ("risk", 2.0, 0.05))


if __name__ == "__main__":
    unittest.main()
