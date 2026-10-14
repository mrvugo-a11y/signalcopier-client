"""Channel results show channel titles, not Telegram ids."""
import asyncio, os, sys, tempfile, types, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier import results as R, engine as E
from copier.store import Store


def deal(pos, entry, t, profit=0):
    return {"position_id": pos, "order": pos, "ticket": pos * 10 + entry, "entry": entry, "time": t,
            "profit": profit, "swap": 0, "commission": 0, "fee": 0, "magic": 0}

DEALS = [deal(1, 0, 1), deal(1, 1, 2, 5), deal(2, 0, 1), deal(2, 1, 2, -1), deal(3, 0, 1), deal(3, 1, 2, 2)]


class Names(unittest.TestCase):
    def setUp(self):
        self.store = Store(os.path.join(tempfile.mkdtemp(), "t.json"))

    def test_removed_channel_uses_stored_title(self):
        self.store.put(-1001, 5, {"tickets": [1], "channel_name": "Gold VIP"})
        cfg = {"channels": {}}                                    # channel no longer selected
        rows = R.aggregate(DEALS[:2], R.ticket_map(self.store.data), R.known_names(cfg, self.store.data))
        self.assertEqual(rows[0]["name"], "Gold VIP")

    def test_current_channel_name_and_prefix_variants(self):
        cfg = {"channels": {"-1001234": {"name": "Forex Pro"}}}
        rows = R.aggregate(DEALS[:2], {1: "1234"}, R.known_names(cfg, {}))
        self.assertEqual(rows[0]["name"], "Forex Pro")
        rows = R.aggregate(DEALS[:2], {1: "-1234"}, R.known_names(cfg, {}))
        self.assertEqual(rows[0]["name"], "Forex Pro")

    def test_name_equal_to_id_is_not_a_title(self):
        rows = R.aggregate(DEALS[:2], {1: "-1009"}, R.known_names({"channels": {"-1009": {"name": "-1009"}}}, {}))
        self.assertEqual(rows[0]["name"], "Unknown channel (-1009)")

    def test_fallback_then_backfill_and_remembered(self):
        tm = {1: "-1001", 2: "-1002", 3: "-1003"}
        rows = R.aggregate(DEALS, tm, R.known_names({}, self.store.data))
        self.assertEqual(sorted(R.missing_names(rows)), ["-1001", "-1002", "-1003"])
        asked = []
        def resolver(ids): asked.append(sorted(ids)); return {"-1001": "Alpha", "-1002": "@betasignals"}
        self.assertTrue(R.backfill_names({}, rows, resolver, self.store))
        names = {r["channel"]: r["name"] for r in rows}
        self.assertEqual(names, {"-1001": "Alpha", "-1002": "@betasignals", "-1003": "Unknown channel (-1003)"})
        self.assertEqual(Store(self.store.path).data["names"]["-1001"], "Alpha")    # saved for next time
        self.assertEqual(asked, [["-1001", "-1002", "-1003"]])

    def test_backfill_survives_resolver_failure(self):
        rows = R.aggregate(DEALS[:2], {1: "-1001"}, {})
        def bad(ids): raise RuntimeError("offline")
        self.assertFalse(R.backfill_names({}, rows, bad, self.store))
        self.assertEqual(rows[0]["name"], "Unknown channel (-1001)")

    def test_nothing_to_resolve_skips_telegram(self):
        rows = R.aggregate(DEALS[:2], {1: "-1001"}, {"-1001": "A"})
        self.assertFalse(R.backfill_names({}, rows, lambda ids: self.fail("should not ask")))

    def test_untagged_row_stays(self):
        rows = R.aggregate([deal(9, 0, 1) | {"magic": 7}, deal(9, 1, 2, 1)], {}, magic=7, include_untagged=True)
        self.assertEqual(rows[0]["name"], R.UNKNOWN); self.assertEqual(R.missing_names(rows), [])


class FakeClient:
    def __init__(self, entities, dialogs, authorized=True):
        self.entities, self.dialogs, self.auth = entities, dialogs, authorized
    async def is_user_authorized(self): return self.auth
    async def get_entity(self, i):
        if i in self.entities: return self.entities[i]
        raise ValueError("not cached")
    async def iter_dialogs(self):
        for d in self.dialogs: yield d


class Resolve(unittest.TestCase):
    def run_with(self, client, ids):
        svc = types.SimpleNamespace(get_client=lambda cfg: _ret(client))
        old = E.service; E.service = lambda: svc
        try: return asyncio.run(E._resolve_titles({}, ids))
        finally: E.service = old

    def test_entity_dialog_and_username(self):
        ent = {-1001: types.SimpleNamespace(title="Alpha"), -1002: types.SimpleNamespace(title="", username="beta")}
        dlg = [types.SimpleNamespace(id=-1003, name="Gamma", entity=None)]
        self.assertEqual(self.run_with(FakeClient(ent, dlg), ["-1001", "-1002", "-1003", "-1004"]),
                         {"-1001": "Alpha", "-1002": "@beta", "-1003": "Gamma"})

    def test_not_logged_in_returns_nothing(self):
        self.assertEqual(self.run_with(FakeClient({}, [], authorized=False), ["-1"]), {})

    def test_resolve_titles_never_raises(self):
        old = E.service
        E.service = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        try: self.assertEqual(E.resolve_titles({}, ["-1"]), {})
        finally: E.service = old


async def _ret(v): return v


class StoreNames(unittest.TestCase):
    def test_put_and_set_names(self):
        s = Store(os.path.join(tempfile.mkdtemp(), "t.json"))
        s.put(-5, 1, {"tickets": [1], "channel_name": "Five"}); s.set_names({-6: "Six", -7: None})
        self.assertEqual(Store(s.path).data["names"], {"-5": "Five", "-6": "Six"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
