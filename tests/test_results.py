import os, sys, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier import results as R


def deal(pos, entry, t, profit=0, swap=0, commission=0, fee=0, magic=0, order=None):
    return {"position_id": pos, "order": order or pos, "ticket": pos * 10 + entry, "entry": entry, "time": t,
            "profit": profit, "swap": swap, "commission": commission, "fee": fee, "magic": magic}


class TestAggregate(unittest.TestCase):
    def test_groups_by_channel_with_costs(self):
        deals = [deal(1, 0, 100, commission=-1), deal(1, 1, 200, profit=20, swap=-0.5, commission=-1),   # A +17.5
                 deal(2, 0, 100), deal(2, 1, 210, profit=-10),                                            # A -10
                 deal(3, 0, 100), deal(3, 1, 220, profit=5),                                              # B +5
                 deal(4, 0, 100)]                                                                         # A still open
        rows = R.aggregate(deals, {1: "A", 2: "A", 3: "B", 4: "A"}, {"A": "Gold", "B": "FX"})
        a = next(r for r in rows if r["channel"] == "A")
        self.assertEqual((a["profit"], a["loss"], a["net"], a["trades"], a["win_rate"]), (17.5, -10.0, 7.5, 2, 50.0))
        self.assertEqual([r["channel"] for r in rows], ["A", "B"])   # sorted by net
        self.assertEqual(a["name"], "Gold")

    def test_period_and_partial_close(self):
        deals = [deal(1, 0, 10), deal(1, 1, 50, profit=3), deal(1, 1, 500, profit=4), deal(2, 0, 10), deal(2, 1, 60, profit=9)]
        rows = R.aggregate(deals, {1: "A", 2: "A"}, since=400)
        self.assertEqual((rows[0]["trades"], rows[0]["net"]), (1, 7.0))

    def test_untagged_only_with_our_magic(self):
        deals = [deal(5, 0, 1, magic=77), deal(5, 1, 2, profit=-2), deal(6, 0, 1, magic=9), deal(6, 1, 2, profit=100)]
        self.assertEqual(R.aggregate(deals, {}), [])
        rows = R.aggregate(deals, {}, magic=77, include_untagged=True)
        self.assertEqual((rows[0]["channel"], rows[0]["net"], rows[0]["trades"]), (R.UNKNOWN, -2.0, 1))

    def test_ticket_map_and_matching_by_order(self):
        tm = R.ticket_map({"tickets": {"11": "-100"}, "signals": {"-200:5": {"tickets": [22]}}})
        self.assertEqual(tm, {11: "-100", 22: "-200"})
        rows = R.aggregate([deal(99, 0, 1, order=22), deal(99, 1, 2, profit=1, order=23)], tm)
        self.assertEqual(rows[0]["channel"], "-200")

    def test_period_start(self):
        now = time.time()
        self.assertEqual(R.period_start(None, now), 0)
        self.assertAlmostEqual(R.period_start(7, now), now - 7 * 86400)
        self.assertLessEqual(R.period_start("today", now), now)

    def test_store_keeps_permanent_map(self):
        import tempfile
        from copier.store import Store
        s = Store(os.path.join(tempfile.mkdtemp(), "t.json")); s.put(-1, 3, {"tickets": [7, 8]})
        self.assertEqual(s.data["tickets"], {"7": "-1", "8": "-1"})

    def test_copy_has_no_dashes(self):
        src = open(os.path.join(os.path.dirname(__file__), "..", "main.py"), encoding="utf-8").read()
        block = src[src.index("class ResultsWindow"):src.index("# ------------------------------------------------------------------ channel picker")]
        import re
        for s in re.findall(r'"([^"]*[A-Za-z][^"]*)"', block):
            if " " in s: self.assertNotRegex(s, r"\w-\w|\s-\s|—|–", s)


if __name__ == "__main__":
    unittest.main()
