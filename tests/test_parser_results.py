import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier.parser import parse, parse_update, Signal, Command

RESULTS = ["XAU/USD SELL hit TP1 +75 pips", "GOLD BUY TP2 hit +120 pips ✅", "XAUUSD SL hit -50 pips",
           "TP1 reached 🎯", "Sell gold +75 pips running", "XAUUSD BUY closed +40 pips profit",
           "Gold sell in profit +30 pips", "EURUSD BUY TP3 hit ✅✅ +90 pips", "XAUUSD SELL stopped out",
           "GOLD SELL 60 pips booked 💰"]


class ResultMessageTests(unittest.TestCase):
    def test_results_never_signals(self):
        for m in RESULTS:
            self.assertNotIsInstance(parse(m), Signal, m)

    def test_results_never_modify(self):
        for m in RESULTS:
            self.assertIsNone(parse_update(m), m)

    def test_exact_bug_message_is_tp_hit(self):
        c = parse("XAU/USD SELL hit TP1 +75 pips")
        self.assertIsInstance(c, Command)
        self.assertEqual((c.action, c.value), ("TP_HIT", 1))

    def test_variants_commands(self):
        self.assertEqual(parse("GOLD BUY TP2 hit +120 pips ✅").action, "TP_HIT")
        self.assertEqual(parse("XAUUSD SL hit -50 pips").action, "SL_HIT")
        self.assertEqual(parse("TP1 reached 🎯").action, "TP_HIT")

    def test_break_even_still_works(self):
        for m in ["TP1 hit +75 pips, move SL to entry", "Move SL to break even", "SL to BE"]:
            self.assertEqual(parse(m).action, "MOVE_SL_BE", m)

    def test_real_signals_still_parse(self):
        s = parse("XAUUSD SELL 2350-2355 SL 2360 TP1 2345")
        self.assertIsInstance(s, Signal)
        self.assertEqual((s.side, s.entry, s.entry_high, s.sl, s.tps), ("SELL", 2350, 2355, 2360, [2345]))
        s = parse("GOLD BUY NOW 2340\nSL 2330\nTP1 2345\nTP2 2350")
        self.assertEqual((s.side, s.symbol, s.sl, s.tps), ("BUY", "XAUUSD", 2330, [2345, 2350]))
        s = parse("XAUUSD BUY 2340 SL 2330 TP1 2345 (+50 pips) TP2 2350 (+100 pips)")
        self.assertIsInstance(s, Signal)
        self.assertEqual(s.sl, 2330)

    def test_update_with_new_values_still_works(self):
        c = parse_update("SL 2360 TP 2340")
        self.assertEqual((c.action, c.sl, c.tps), ("MODIFY", 2360, [2340]))


if __name__ == "__main__":
    unittest.main()
