"""Break even after TP1: executor watcher and the setting itself."""
import os, sys, types, unittest
from collections import namedtuple
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier import executor, config as C, settings_schema as S
from copier.parser import Command
from test_executor import fake_mt5

Pos = namedtuple("Pos", "ticket symbol type price_open sl tp volume magic")
Deal = namedtuple("Deal", "position_id entry reason price symbol")
Info = namedtuple("Info", "name point digits volume_step volume_min volume_max trade_tick_size trade_tick_value filling_mode trade_stops_level")
Tick = namedtuple("Tick", "bid ask")


class BE(unittest.TestCase):
    def setUp(self):
        m = self.m = executor.mt5 = fake_mt5()
        m.POSITION_TYPE_BUY, m.POSITION_TYPE_SELL = 0, 1
        m.TRADE_ACTION_SLTP = 6; m.DEAL_REASON_TP = 5; m.DEAL_ENTRY_OUT = 1
        self.info = Info("XAUUSDm", 0.01, 2, 0.01, 0.01, 100, 0.01, 0.01, 1, 0)
        m.symbol_info = lambda s: self.info
        self.tick = Tick(1956.00, 1956.20)
        m.symbol_info_tick = lambda s: self.tick
        self.pos = {}; self.deals = {}
        m.positions_get = lambda ticket=None, **k: [self.pos[ticket]] if ticket in self.pos else []
        m.history_deals_get = lambda position=None, **k: self.deals.get(position, [])
        self.logs = []
        self.ex = executor.Executor({}, self.logs.append)
        self.rec = {"tickets": [1, 2, 3], "tp1": 1955.0, "tps": [1955, 1960, 1965]}

    def buy(self, t, tp, sl=1940.0):
        self.pos[t] = Pos(t, "XAUUSDm", 0, 1950.0, sl, tp, 0.01, 7)

    def sltp(self):
        return [r for r in self.m.sent if r["action"] == 6]

    def test_tp1_leg_closed_at_tp_moves_others(self):
        self.buy(2, 1960); self.buy(3, 1965)
        self.deals[1] = [Deal(1, 1, 5, 1955.0, "XAUUSDm")]
        self.assertTrue(self.ex.check_break_even(self.rec, {}))
        self.assertEqual([(r["position"], r["sl"]) for r in self.sltp()], [(2, 1950.0), (3, 1950.0)])

    def test_price_hit_tp1_while_tp1_leg_still_open(self):
        self.buy(1, 0); self.buy(2, 1960)
        self.ex.check_break_even({"tickets": [1, 2], "tp1": 1955.0}, {})
        self.assertEqual([r["position"] for r in self.sltp()], [2])

    def test_single_trade_price_hit(self):
        self.buy(1, 1970)
        self.ex.check_break_even({"tickets": [1], "tp1": 1955.0}, {})
        self.assertEqual([r["position"] for r in self.sltp()], [1])

    def test_no_move_before_tp1(self):
        self.tick = Tick(1952.0, 1952.2)
        self.buy(1, 1955); self.buy(2, 1960)
        self.assertFalse(self.ex.check_break_even(self.rec, {}))
        self.assertEqual(self.sltp(), [])

    def test_tp1_closed_by_stop_loss_does_nothing(self):
        self.buy(2, 1960)
        self.tick = Tick(1945.0, 1945.2)
        self.deals[1] = [Deal(1, 1, 4, 1940.0, "XAUUSDm")]
        self.ex.check_break_even(self.rec, {})
        self.assertEqual(self.sltp(), [])

    def test_sell_side(self):
        self.tick = Tick(1944.8, 1945.0)
        self.pos[2] = Pos(2, "XAUUSDm", 1, 1950.0, 1960.0, 1940.0, 0.01, 7)
        self.deals[1] = [Deal(1, 1, 5, 1945.0, "XAUUSDm")]
        self.ex.check_break_even({"tickets": [1, 2], "tp1": 1945.0}, {})
        self.assertEqual([(r["position"], r["sl"]) for r in self.sltp()], [(2, 1950.0)])

    def test_min_stop_distance_logged_once_and_retried(self):
        self.info = self.info._replace(trade_stops_level=1000)      # 10.00 away needed, price is 6.00 away
        self.buy(2, 1960); self.deals[1] = [Deal(1, 1, 5, 1955.0, "XAUUSDm")]
        warned = set()
        self.assertFalse(self.ex.check_break_even(self.rec, {}, warned))
        self.assertFalse(self.ex.check_break_even(self.rec, {}, warned))
        self.assertEqual(self.sltp(), [])
        msgs = [l for l in self.logs if "minimum stop distance" in l]
        self.assertEqual(len(msgs), 1)
        self.assertIn("Could not move the stop loss of trade 2", msgs[0])

    def test_broker_rejection_logged(self):
        R = namedtuple("R", "retcode order deal comment")
        self.m.order_send = lambda r: (self.m.sent.append(r), R(10016, 0, 0, "Invalid stops"))[1]
        self.buy(2, 1960); self.deals[1] = [Deal(1, 1, 5, 1955.0, "XAUUSDm")]
        self.assertFalse(self.ex.check_break_even(self.rec, {}))
        self.assertTrue(any("refused to move the stop loss" in l and "Invalid stops" in l for l in self.logs))

    def test_already_at_break_even_skipped(self):
        self.buy(2, 1960, sl=1950.0); self.deals[1] = [Deal(1, 1, 5, 1955.0, "XAUUSDm")]
        self.assertTrue(self.ex.check_break_even(self.rec, {}))
        self.assertEqual(self.sltp(), [])

    def test_old_record_uses_signal_tp1(self):
        self.buy(1, 0); self.buy(2, 1960)
        self.ex.check_break_even({"tickets": [1, 2], "tps": [1955, 1960]}, {})
        self.assertEqual([r["position"] for r in self.sltp()], [2])

    def test_channel_breakeven_command_still_works(self):
        self.buy(1, 1955)
        self.m.TRADE_ACTION_SLTP = 6
        self.ex.apply(Command("MOVE_SL_BE"), [1], {"be_after_tp1": False})
        self.assertEqual([(r["position"], r["sl"]) for r in self.sltp()], [(1, 1950.0)])

    def test_open_remembers_tp1(self):
        self.ex.open_signal(__import__("copier.parser", fromlist=["parse"]).parse("GOLD BUY 1950 SL 1940 TP1 1955 TP2 1960"),
                            {"lot_mode": "fixed", "fixed_lot": 0.01, "magic_number": 7, "max_spread_points": 0})
        self.assertEqual(self.ex.last_tp1, 1955)


class Setting(unittest.TestCase):
    def test_default_off_and_label(self):
        self.assertIs(C.DEFAULT_SETTINGS["be_after_tp1"], False)
        f = [x for x in S.TRADE_FIELDS if x[0] == "be_after_tp1"][0]
        self.assertEqual((f[1], f[2]), ("Move stop loss to break even after TP1 is hit", "bool"))
        for t in (f[1], f[4]): self.assertNotIn("-", t)

    def test_persists_and_old_config_gets_default(self):
        import tempfile, json
        d = tempfile.mkdtemp(); os.environ["SIGNALPILOT_HOME"] = d
        try:
            with open(os.path.join(d, "config.json"), "w") as fh:     # config saved by 1.3.0, no new key
                json.dump({"default_settings": {"fixed_lot": 0.05}}, fh)
            cfg = C.load()
            self.assertIs(cfg["default_settings"]["be_after_tp1"], False)
            self.assertEqual(cfg["default_settings"]["fixed_lot"], 0.05)
            cfg["default_settings"]["be_after_tp1"] = True; C.save(cfg)
            self.assertIs(C.load()["default_settings"]["be_after_tp1"], True)
        finally:
            os.environ.pop("SIGNALPILOT_HOME", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
