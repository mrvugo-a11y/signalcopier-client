"""MT5 history reads survive the 'returned a result with an exception set' SystemError."""
import os, sys, types, unittest
from unittest import mock
from datetime import datetime, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier import mt5hist as H, share as SH, results as R


class FakeMT5:
    """Like the real package: refuses dates near/before 1970 and can fail N times."""
    def __init__(self, deals=(), fail=0, range_broken=False):
        self.deals, self.fail, self.range_broken, self.calls = list(deals), fail, range_broken, []
    def last_error(self): return (-1, "Terminal: Call failed")
    def account_info(self): return types.SimpleNamespace(balance=1000.0, currency="ZAR")
    def symbol_info(self, s): return types.SimpleNamespace(point=0.00001, digits=5)
    def history_deals_get(self, *a, position=None, **k):
        self.calls.append((a, position))
        if position is not None:
            return tuple(d for d in self.deals if d["position_id"] == position)
        lo, hi = a
        assert lo.tzinfo is None and hi.tzinfo is None
        if lo < datetime(1971, 1, 1) or hi > datetime.now() + timedelta(days=30):
            raise SystemError("<built-in function history_deals_get> returned a result with an exception set")
        if self.fail > 0 or self.range_broken:
            self.fail -= 1
            raise SystemError("<built-in function history_deals_get> returned a result with an exception set")
        return tuple(self.deals)


def deal(pos, entry, t, price, typ=0, profit=0):
    return {"position_id": pos, "order": pos, "ticket": pos * 10 + entry, "entry": entry, "time": t, "price": price,
            "type": typ, "volume": 0.1, "symbol": "EURUSD", "profit": profit, "swap": 0, "commission": 0, "fee": 0, "magic": 0}

DEALS = [deal(1, 0, 1_790_000_000, 1.1), deal(1, 1, 1_790_000_100, 1.102, typ=1, profit=20)]
STORE = {"signals": {"-1001:5": {"tickets": [1], "sl": 1.099}}}
CFG = {"licence_key": "K", "licence_url": "https://x/api/licence"}


class T(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(H.time, "sleep", lambda s: None); p.start(); self.addCleanup(p.stop)

    def test_safe_range_never_epoch(self):
        lo, hi = H.safe_range(0, 10**12)
        self.assertGreaterEqual(lo, H.EARLIEST); self.assertLess(hi, datetime.now() + timedelta(days=3))
        self.assertIsNone(lo.tzinfo)

    def test_share_collect_with_old_buggy_dates_fixed(self):
        rows = SH.collect(CFG, FakeMT5(DEALS), STORE)
        self.assertEqual((rows[0]["id"], rows[0]["trades"], rows[0]["wins"]), ("-1001", 1, 1))

    def test_retry_after_transient_failure(self):
        m = FakeMT5(DEALS, fail=2)

        self.assertEqual(len(H.deals(m, None, None)), 2)

    def test_fallback_per_position_and_logs_last_error(self):

        logs = []
        rows = SH.collect(CFG, FakeMT5(DEALS, range_broken=True), STORE, log=logs.append)
        self.assertEqual(rows[0]["trades"], 1)
        self.assertIn("last_error=(-1", logs[0])

    def test_raises_with_last_error_when_nothing_readable(self):

        with self.assertRaises(RuntimeError) as c:
            H.deals(FakeMT5(DEALS, range_broken=True), None, None)
        self.assertIn("Call failed", str(c.exception))

    def test_results_screen_all_time(self):
        rows, cur = R.fetch({}, None, mt5=FakeMT5(DEALS), store_data=STORE)
        self.assertEqual((cur, rows[0]["trades"]), ("ZAR", 1))

    def test_break_even_position_query(self):
        m = FakeMT5(DEALS, fail=1)
        self.assertEqual(len(H.position_deals(m, 1)), 2)


if __name__ == "__main__":
    unittest.main()
