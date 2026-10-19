"""Bug 1: a reply that is itself the full signal opens a trade. Bug 2: break even is checked before sending."""
import asyncio, os, sys, tempfile, types, unittest
from collections import namedtuple
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier import executor, engine as E
from copier.parser import parse, parse_update, parse_signal, Command
from copier.store import Store
from test_executor import fake_mt5

PARENT = "🚀XAUUSD BUY NOW"
REPLY = """🌎 Entry Zone: 4162–4159

🛑 Stop Loss: 4150

🤔 TP1: 4165
🤔 TP2: 4168
🤔 TP3: 4172
🤔 TP4: 4184 — LAST & FINAL

🚨 Stay calm. Follow the plan.
🔥 Let the market do the work.
Good luck, traders"""


class ParserGoldex(unittest.TestCase):
    def test_parent_alone_is_nothing(self):
        self.assertIsNone(parse(PARENT))

    def test_reply_alone_is_values_only(self):
        self.assertIsNone(parse(REPLY))          # no symbol, no direction
        u = parse_update(REPLY)
        self.assertEqual((u.action, u.sl, u.tps), ("MODIFY", 4150, [4165, 4168, 4172, 4184]))

    def test_parent_plus_reply_en_dash_emojis_last_final(self):
        s = parse_signal(PARENT + "\n" + REPLY)
        self.assertEqual((s.side, s.symbol, s.entry, s.entry_high, s.sl), ("BUY", "XAUUSD", 4159, 4162, 4150))
        self.assertEqual(s.tps, [4165, 4168, 4172, 4184])


class FakeEv:
    def __init__(self, mid, text, reply_to=None, parent_text=None):
        self.id, self.raw_text, self.chat_id = mid, text, -100
        async def grm(): return types.SimpleNamespace(raw_text=parent_text) if parent_text else None
        self.message = types.SimpleNamespace(reply_to_msg_id=reply_to, get_reply_message=grm)


class EngineGoldex(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.logs = []
        e = self.e = E.Engine(self.logs.append, None)
        e.cfg = {"channels": {"-100": {"name": "Goldex Prime Signals"}}}
        e._st = lambda c: {"enabled": True, "follow_edits": True}
        e.store = Store(os.path.join(self.d, "t.json"))
        self.opened, self.applied = [], []
        e.ex = types.SimpleNamespace(open_signal=lambda s, st: self.opened.append(s) or [555],
                                     apply=lambda c, t, st, w=None: self.applied.append((c.action, t)) or [],
                                     last_tp1=4165)

    def run_(self, coro): asyncio.run(coro)

    def test_reply_full_signal_opens_trade(self):
        self.run_(self.e._on_new(FakeEv(10, PARENT)))
        self.run_(self.e._on_new(FakeEv(11, REPLY, reply_to=10, parent_text=PARENT)))
        self.assertEqual(len(self.opened), 1)
        s = self.opened[0]
        self.assertEqual((s.side, s.symbol, s.sl, s.tps), ("BUY", "XAUUSD", 4150, [4165, 4168, 4172, 4184]))
        self.assertFalse(any("nothing to do" in l for l in self.logs), self.logs)
        # a later "TP1 hit" reply to the PARENT now finds the trade
        self.run_(self.e._on_new(FakeEv(12, "TP1 hit ✅", reply_to=10)))
        self.assertEqual(self.applied, [("TP_HIT", [555])])

    def test_reply_with_existing_trade_is_modify(self):
        self.e.store.put(-100, 10, {"symbol": "XAUUSD", "side": "BUY", "tickets": [9]})
        self.run_(self.e._on_new(FakeEv(11, REPLY, reply_to=10, parent_text=PARENT)))
        self.assertEqual(self.opened, []); self.assertEqual(self.applied, [("MODIFY", [9])])

    def test_parent_without_direction_does_not_open(self):
        self.run_(self.e._on_new(FakeEv(11, REPLY, reply_to=10, parent_text="Good morning traders")))
        self.assertEqual(self.opened, [])

    def test_edited_reply_opens_trade(self):
        self.run_(self.e._on_edit(FakeEv(11, REPLY, reply_to=10, parent_text=PARENT)))
        self.assertEqual(len(self.opened), 1)


Pos = namedtuple("Pos", "ticket symbol type price_open sl tp volume magic")
Info = namedtuple("Info", "name point digits volume_step volume_min volume_max trade_tick_size trade_tick_value filling_mode trade_stops_level trade_freeze_level")
Tick = namedtuple("Tick", "bid ask")


class MoveBE(unittest.TestCase):
    def setUp(self):
        m = self.m = executor.mt5 = fake_mt5()
        m.POSITION_TYPE_BUY, m.POSITION_TYPE_SELL, m.TRADE_ACTION_SLTP = 0, 1, 6
        self.info = Info("XAUUSDm", 0.001, 3, 0.01, 0.01, 100, 0.001, 0.001, 1, 0, 0)
        m.symbol_info = lambda s: self.info
        self.tick = Tick(4155.000, 4155.200)
        m.symbol_info_tick = lambda s: self.tick
        self.pos = {145645587: Pos(145645587, "XAUUSDm", 0, 4160.1234, 4150.0, 4165.0, 0.01, 7)}
        m.positions_get = lambda ticket=None, **k: [self.pos[ticket]] if ticket in self.pos else []
        self.logs = []; self.ex = executor.Executor({}, self.logs.append)

    def sltp(self): return [r for r in self.m.sent if r["action"] == 6]

    def test_buy_in_loss_not_sent_logged_once_then_retried(self):
        w = set()
        left = self.ex.apply(Command("MOVE_SL_BE"), [145645587], {}, w)
        self.assertEqual(left, [145645587]); self.assertEqual(self.sltp(), [])
        self.ex.move_to_be_now([self.pos[145645587]], w)
        msgs = [l for l in self.logs if "break even" in l]
        self.assertEqual(len(msgs), 1)
        self.assertIn("Will retry when possible", msgs[0]); self.assertNotIn("-", msgs[0])
        self.tick = Tick(4170.0, 4170.2)            # now in profit: retry succeeds, price normalised
        self.assertEqual(self.ex.move_to_be_now([self.pos[145645587]], w), [])
        self.assertEqual(self.sltp()[0]["sl"], 4160.123)

    def test_freeze_level_blocks(self):
        self.info = self.info._replace(trade_freeze_level=20000)   # 20.000 away needed
        self.tick = Tick(4170.0, 4170.2)
        self.assertEqual(self.ex.apply(Command("MOVE_SL_BE"), [145645587], {}, set()), [145645587])
        self.assertEqual(self.sltp(), [])


if __name__ == "__main__":
    unittest.main()
