import os, sys, tempfile, threading, types, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier.parser import parse, Signal
from copier.store import Store

BUY = ("⚡️ #GBPUSD\n\nНомер сделки: 47941683\n\nСделка на 0,5% риска.\n\nТип ордера: ПОКУПКА\n"
       "Точка входа: 1.32262\nСтоп лосс: 1.31962\nТейк профит: 1.32562\n\n#Бесплатный_сигнал")
SELL = ("⚡️ #EURUSD\n\nНомер сделки: 47941690\n\nСделка на 0,5% риска.\n\nТип ордера: ПРОДАЖА\n"
        "Точка входа: 1.16500\nСтоп-лосс: 1.16800\nТейк-профит: 1.16200\n\n#Бесплатный_сигнал")


class RussianSignalTests(unittest.TestCase):
    def test_buy(self):
        s = parse(BUY)
        self.assertIsInstance(s, Signal)
        self.assertEqual((s.side, s.symbol, s.order_type, s.entry, s.sl, s.tps, s.trade_id),
                         ("BUY", "GBPUSD", "MARKET", 1.32262, 1.31962, [1.32562], "47941683"))

    def test_sell(self):
        s = parse(SELL)
        self.assertEqual((s.side, s.symbol, s.entry, s.sl, s.tps), ("SELL", "EURUSD", 1.165, 1.168, [1.162]))

    def test_case_insensitive_and_synonyms(self):
        s = parse("#GBPUSD\nТип ордера: купить\nвход: 1.30\nстоп лосс: 1.29\nтейк профит: 1.32")
        self.assertEqual((s.side, s.entry, s.sl, s.tps), ("BUY", 1.30, 1.29, [1.32]))
        s = parse("#GBPUSD\nПродавать\nВход: 1.30\nСтоп-Лосс: 1.31\nТейк-Профит: 1.28")
        self.assertEqual((s.side, s.entry, s.sl, s.tps), ("SELL", 1.30, 1.31, [1.28]))

    def test_trade_number_not_price(self):
        s = parse("#GBPUSD\nНомер сделки: 12345\nПОКУПКА\nСтоп лосс: 1.31962\nТейк профит: 1.32562")
        self.assertIsNone(s.entry)

    def test_analysis_ignored(self):
        for t in ["#EURUSD\nПОКУПКА 1.1150–1.1180\nСтоп лосс: 1.1100\nТейк профит: 1.1250",
                  "#EURUSD покупать в зоне 1.1150\nСтоп лосс: 1.1100\nТейк профит: 1.1250",
                  "#EURUSD ПРОДАЖА 1.1180\nСтоп / отмена: 1.1220\nТейк профит: 1.1100"]:
            self.assertIsNone(parse(t), t)


class RepostDedupeTests(unittest.TestCase):
    def test_repost_opened_once(self):
        from copier.engine import Engine
        d = tempfile.mkdtemp()
        opened = []
        fake = types.SimpleNamespace(
            store=Store(os.path.join(d, "t.json")), mt5_lock=threading.Lock(), log=lambda m: None,
            ex=types.SimpleNamespace(open_signal=lambda sig, st: opened.append(sig) or [len(opened)]),
            _name=lambda c: str(c))
        Engine._open(fake, -100, 1, parse(BUY), {})
        Engine._open(fake, -100, 2, parse(BUY), {})      # repost, same trade number
        self.assertEqual(len(opened), 1)
        Engine._open(fake, -200, 3, parse(BUY), {})      # other channel, same number -> separate
        self.assertEqual(len(opened), 2)
        # survives restart
        fake.store = Store(os.path.join(d, "t.json"))
        Engine._open(fake, -100, 4, parse(BUY), {})
        self.assertEqual(len(opened), 2)


if __name__ == "__main__":
    unittest.main()
