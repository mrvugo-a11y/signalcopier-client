"""Status/result replies ('Trade activated', 'BUY hit SL -75 pips') must never open trades,
including cross-chat replies quoting a signal from another channel."""
import asyncio, os, sys, tempfile, types, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier import engine as E
from copier.parser import parse, parse_signal, parse_update, Signal
from copier.store import Store

VIP = "XAU/USD BUY LIMIT @ 4125 🔵\nTP1 4135\nTP2 4145\nSL 4115"
STATUS = ["Trade activated", "TP1 hit", "running in profit", "SL hit", "entry triggered",
          "XAU/USD BUY hit SL -75 pips", "XAU/USD BUY hit TP1 +100 pips"]


class Parser(unittest.TestCase):
    def test_status_never_signal(self):
        for t in STATUS:
            self.assertNotIsInstance(parse(t), Signal, t)
            self.assertIsNone(parse_signal(VIP + "\n" + t) if "hit" in t.lower() or "activ" in t.lower() else None, t)

    def test_sl_result_not_modify(self):
        self.assertIsNone(parse_update("XAU/USD BUY hit SL -75 pips"))

    def test_real_signal_still_parses(self):
        self.assertIsInstance(parse(VIP), Signal)


class Ev:
    def __init__(self, mid, text, reply_to=None, parent_text=None, peer=None, quote=None):
        self.id, self.raw_text, self.chat_id = mid, text, -100
        async def grm(): return types.SimpleNamespace(raw_text=parent_text) if parent_text else None
        self.message = types.SimpleNamespace(reply_to_msg_id=reply_to, get_reply_message=grm,
            reply_to=types.SimpleNamespace(reply_to_peer_id=peer, quote_text=quote) if reply_to else None)


class Engine(unittest.TestCase):
    def setUp(self):
        e = self.e = E.Engine(lambda *_: None, None)
        e.cfg = {"channels": {"-100": {"name": "XTREME FREE GOLD SIGNALS"}}}
        e._st = lambda c: {"enabled": True, "follow_edits": True}
        e.store = Store(os.path.join(tempfile.mkdtemp(), "t.json"))
        self.opened = []
        e.ex = types.SimpleNamespace(open_signal=lambda s, st: self.opened.append(s) or [1],
                                     apply=lambda *a, **k: [], last_tp1=None)

    def go(self, ev, edit=False): asyncio.run((self.e._on_edit if edit else self.e._on_new)(ev))

    def test_standalone(self):
        for i, t in enumerate(STATUS): self.go(Ev(10 + i, t))
        self.assertEqual(self.opened, [])

    def test_reply_to_full_signal_parent_same_chat(self):
        for i, t in enumerate(STATUS): self.go(Ev(10 + i, t, reply_to=5, parent_text=VIP))
        self.assertEqual(self.opened, [])

    def test_cross_chat_quote_rendered_into_text(self):
        for t in ("Trade activated", "XAU/USD BUY hit SL -75 pips"):
            self.go(Ev(20, VIP + "\n" + t, reply_to=7, parent_text=VIP, peer=12345, quote=VIP))
            self.go(Ev(21, VIP + "\n" + t, reply_to=7, parent_text=VIP, peer=12345), edit=True)
        self.assertEqual(self.opened, [])

    def test_reply_duplicating_copied_parent_not_reopened(self):
        self.go(Ev(5, VIP)); self.assertEqual(len(self.opened), 1)
        self.go(Ev(6, VIP, reply_to=5, parent_text=VIP))
        self.assertEqual(len(self.opened), 1)


if __name__ == "__main__": unittest.main()
