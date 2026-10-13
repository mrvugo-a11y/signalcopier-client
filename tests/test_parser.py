import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier.parser import parse, parse_signal, parse_command, parse_update, Signal, Command


class SignalTests(unittest.TestCase):
    def sig(self, text):
        s = parse(text)
        self.assertIsInstance(s, Signal, text)
        return s

    def test_basic_gold(self):
        s = self.sig("XAUUSD BUY @ 1950\nSL 1940\nTP1 1955\nTP2 1960\nTP3 1970")
        self.assertEqual((s.side, s.symbol, s.order_type, s.entry, s.sl), ("BUY", "XAUUSD", "MARKET", 1950, 1940))
        self.assertEqual(s.tps, [1955, 1960, 1970])

    def test_gold_alias_emoji(self):
        s = self.sig("🔥 GOLD SELL NOW 2345.50 🔥\n❌ SL: 2352\n✅ TP1: 2340\n✅ TP2: 2335")
        self.assertEqual((s.side, s.symbol, s.entry, s.sl, s.tps), ("SELL", "XAUUSD", 2345.5, 2352, [2340, 2335]))

    def test_range(self):
        s = self.sig("BUY XAUUSD 1948-1945\nSL 1940\nTP 1955\nTP 1960")
        self.assertEqual((s.entry, s.entry_high), (1945, 1948))
        self.assertEqual(s.tps, [1955, 1960])

    def test_buy_limit(self):
        s = self.sig("EURUSD BUY LIMIT 1.0850\nSL 1.0820\nTP 1.0900")
        self.assertEqual((s.order_type, s.entry, s.sl, s.tps), ("LIMIT", 1.085, 1.082, [1.09]))

    def test_sell_stop(self):
        s = self.sig("GBPJPY SELL STOP @ 187.50 SL 188.20 TP1 186.80 TP2 186.00")
        self.assertEqual((s.side, s.order_type, s.symbol, s.entry, s.sl), ("SELL", "STOP", "GBPJPY", 187.5, 188.2))
        self.assertEqual(s.tps, [186.8, 186.0])

    def test_slash_pair(self):
        s = self.sig("EUR/USD SELL 1.0950\nStop Loss: 1.0980\nTake Profit 1: 1.0920\nTake Profit 2: 1.0900")
        self.assertEqual((s.symbol, s.side, s.sl, s.tps), ("EURUSD", "SELL", 1.098, [1.092, 1.09]))

    def test_no_entry_market(self):
        s = self.sig("Sell USDJPY now\nSL 151.20\nTP 150.20")
        self.assertIsNone(s.entry)
        self.assertEqual((s.symbol, s.sl, s.tps), ("USDJPY", 151.2, [150.2]))

    def test_tp_open(self):
        s = self.sig("BUY GOLD 2300\nSL 2290\nTP1 2305\nTP2 2310\nTP3 OPEN")
        self.assertEqual(s.tps, [2305, 2310, None])

    def test_indices(self):
        s = self.sig("US30 BUY 39000\nSL 38900\nTP 39150")
        self.assertEqual((s.symbol, s.entry, s.sl, s.tps), ("US30", 39000, 38900, [39150]))
        s = self.sig("NASDAQ sell 18250 sl 18320 tp 18150 tp 18050")
        self.assertEqual((s.symbol, s.side, s.entry, s.tps), ("NAS100", "SELL", 18250, [18150, 18050]))

    def test_crypto(self):
        s = self.sig("#BTCUSDT LONG\nEntry: 65000\nSL: 63500\nTarget 1: 66000\nTarget 2: 67000")
        self.assertEqual((s.symbol, s.side, s.entry, s.sl, s.tps), ("BTCUSD", "BUY", 65000, 63500, [66000, 67000]))

    def test_tp_slash_list(self):
        s = self.sig("XAUUSD SELL 2400 SL 2410 TP 2395/2390/2380")
        self.assertEqual(s.tps, [2395, 2390, 2380])

    def test_hashtag_and_lowercase(self):
        s = self.sig("#gbpusd buy at 1.2650 sl=1.2620 tp=1.2700")
        self.assertEqual((s.symbol, s.entry, s.sl, s.tps), ("GBPUSD", 1.265, 1.262, [1.27]))

    def test_comma_decimal(self):
        s = self.sig("XAUUSD BUY 1950,5 SL 1940,5 TP 1960,5")
        self.assertEqual((s.entry, s.sl, s.tps), (1950.5, 1940.5, [1960.5]))

    def test_entry_zone_words(self):
        s = self.sig("GOLD BUY ZONE 2310 - 2305\nSL 2300\nTP1 2315\nTP2 2320")
        self.assertEqual((s.entry, s.entry_high, s.sl), (2305, 2310, 2300))

    def test_sell_limit_lines(self):
        s = self.sig("SELL LIMIT\nAUDUSD\nEntry 0.6650\nSL 0.6680\nTP 0.6600")
        self.assertEqual((s.symbol, s.order_type, s.entry), ("AUDUSD", "LIMIT", 0.665))

    def test_buy_stop_hyphen(self):
        s = self.sig("USDCAD Buy-Stop 1.3700 SL 1.3660 TP 1.3760")
        self.assertEqual((s.order_type, s.entry), ("STOP", 1.37))

    def test_not_signal(self):
        for txt in ["Good morning traders!", "Gold is looking bullish today, buy the dip", "",
                    "Join our VIP channel", "Results this week +450 pips"]:
            self.assertNotIsInstance(parse(txt), Signal, txt)


class CommandTests(unittest.TestCase):
    def cmd(self, text, action, value=None):
        c = parse(text)
        self.assertIsInstance(c, Command, text)
        self.assertEqual(c.action, action, text)
        if value is not None:
            self.assertAlmostEqual(c.value, value)

    def test_close(self):
        for t in ["close", "Close now", "CLOSE ALL", "close everything", "Exit now", "Close trade"]:
            self.cmd(t, "CLOSE_ALL")

    def test_close_half(self):
        for t in ["close half", "Close 50%", "take partials", "close partial", "Secure partials now"]:
            self.cmd(t, "CLOSE_HALF") if "50%" not in t else self.cmd(t, "CLOSE_PARTIAL", 0.5)

    def test_close_percent(self):
        self.cmd("close 30%", "CLOSE_PARTIAL", 0.3)

    def test_be(self):
        for t in ["move SL to entry", "Move sl to BE", "SL to breakeven", "set BE", "SL to entry price", "break even now"]:
            self.cmd(t, "MOVE_SL_BE")

    def test_move_sl_price(self):
        self.cmd("move SL to 1955", "MOVE_SL", 1955)
        self.cmd("New SL: 1.0870", "MOVE_SL", 1.087)

    def test_tp_hit(self):
        self.cmd("TP1 hit ✅", "TP_HIT", 1)
        self.cmd("TP 2 HIT 🔥 +50 pips", "TP_HIT", 2)
        self.cmd("TP hit", "TP_HIT", 1)

    def test_sl_hit(self):
        self.cmd("SL hit", "SL_HIT")

    def test_cancel(self):
        self.cmd("cancel order", "CANCEL")
        self.cmd("Delete the limit", "CANCEL")

    def test_update(self):
        u = parse_update("SL 1945 TP 1990")
        self.assertEqual((u.action, u.sl, u.tps), ("MODIFY", 1945, [1990]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
