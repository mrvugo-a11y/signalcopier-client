"""Licence cache, offline grace, rejection and tamper protection."""
import json, os, sys, tempfile, time, unittest
from datetime import date, timedelta
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier import licence as L, config as C

URL = "https://site.example/api/licence"
DAY = 86400
FUTURE = (date.today() + timedelta(days=30)).isoformat()
PAST = (date.today() - timedelta(days=1)).isoformat()


class TestLicence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"SIGNALPILOT_HOME": self.tmp.name}); self.env.start()
        self.sleeps = []

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def run_check(self, ask, key="KEY"):
        with mock.patch.object(L, "_ask", side_effect=ask) as m:
            r = L.check_full(key, URL, sleep=self.sleeps.append)
        return r, m.call_count

    def cache(self, age_days, expires=FUTURE, key="KEY"):
        L.save_cache(key, expires, "pro", now=time.time() - age_days * DAY)

    @staticmethod
    def down(*a): raise OSError("connection refused")

    def test_fresh_cache_no_network(self):
        self.cache(0.5)
        (ok, msg, cached), calls = self.run_check(self.down)
        self.assertTrue(ok); self.assertTrue(cached); self.assertEqual(calls, 0); self.assertIn(FUTURE, msg)

    def test_cache_1_to_3_days_server_down_allows(self):
        for age in (1.5, 2.9):
            self.cache(age); self.sleeps.clear()
            (ok, msg, cached), calls = self.run_check(self.down)
            self.assertTrue(ok); self.assertIn("grace", msg); self.assertFalse(cached)
            self.assertEqual(calls, 2); self.assertEqual(self.sleeps, [L.RETRY_DELAY])

    def test_cache_over_3_days_server_down_blocks(self):
        self.cache(3.5)
        (ok, msg, _), calls = self.run_check(self.down)
        self.assertFalse(ok); self.assertEqual(msg, L.UNREACHABLE_MSG); self.assertEqual(calls, 2)

    def test_retry_succeeds_and_refreshes_cache(self):
        self.cache(5)
        answers = [OSError("timeout"), {"valid": True, "expires": FUTURE, "plan": "pro"}]
        def ask(*a):
            x = answers.pop(0)
            if isinstance(x, Exception): raise x
            return x
        (ok, _, _), calls = self.run_check(ask)
        self.assertTrue(ok); self.assertEqual(calls, 2)
        self.assertLess(time.time() - L.load_cache()["last_ok"], 60)

    def test_explicit_rejection_blocks_and_clears(self):
        self.cache(2)   # older than 24h, so the server is asked
        (ok, msg, _), _ = self.run_check(lambda *a: {"valid": False, "message": "licence revoked"})
        self.assertFalse(ok); self.assertIn("revoked", msg); self.assertIsNone(L.load_cache())
        (ok, _, _), _ = self.run_check(self.down)
        self.assertFalse(ok)

    def test_background_refresh_rejection(self):
        self.cache(0.1)
        with mock.patch.object(L, "_ask", return_value={"valid": False, "message": "licence expired or not active"}):
            ok, msg, rejected = L.refresh("KEY", URL, sleep=self.sleeps.append)
        self.assertFalse(ok); self.assertTrue(rejected); self.assertIsNone(L.load_cache())

    def test_background_refresh_down_inside_grace_keeps_running(self):
        self.cache(2)
        with mock.patch.object(L, "_ask", side_effect=self.down):
            ok, _, rejected = L.refresh("KEY", URL, sleep=self.sleeps.append)
        self.assertTrue(ok); self.assertFalse(rejected)

    def test_key_changed_ignores_cache(self):
        self.cache(0.1, key="OLD")
        (ok, msg, _), calls = self.run_check(self.down, key="NEW")
        self.assertFalse(ok); self.assertEqual(calls, 2)
        (ok, _, cached), calls = self.run_check(lambda *a: {"valid": True, "expires": FUTURE}, key="NEW")
        self.assertTrue(ok); self.assertFalse(cached); self.assertEqual(calls, 1)

    def test_cached_expiry_passed(self):
        self.cache(0.1, expires=PAST)
        (ok, _, _), calls = self.run_check(self.down)
        self.assertFalse(ok); self.assertEqual(calls, 2)

    def test_tampered_file_is_no_cache(self):
        self.cache(0.1, expires=PAST)
        p = C.path("licence.dat"); st = json.load(open(p))
        st["cache"]["expires"] = "2099-12-31"; json.dump(st, open(p, "w"))
        self.assertIsNone(L.load_cache())
        (ok, _, _), calls = self.run_check(self.down)
        self.assertFalse(ok); self.assertEqual(calls, 2)
        st["cache"]["sig"] = "0" * 64; json.dump(st, open(p, "w"))
        self.assertIsNone(L.load_cache())
        open(p, "w").write("not json"); self.assertIsNone(L.load_cache())

    def test_other_machine_is_no_cache(self):
        self.cache(0.1)
        with mock.patch.object(L, "machine_id", return_value="another-pc"):
            self.assertIsNone(L.load_cache())


if __name__ == "__main__":
    unittest.main()
