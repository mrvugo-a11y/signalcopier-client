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
        (ok, msg, follow), calls = self.run_check(self.down)
        self.assertTrue(ok); self.assertEqual(follow, L.REFRESH); self.assertEqual(calls, 0); self.assertIn(FUTURE, msg)

    def test_cache_1_to_3_days_server_down_allows(self):
        for age in (1.5, 2.9):
            self.cache(age); self.sleeps.clear()
            (ok, msg, follow), calls = self.run_check(self.down)
            self.assertIs(ok, True); self.assertIn("grace", msg); self.assertIsNone(follow)
            self.assertEqual(calls, 2); self.assertEqual(self.sleeps, [L.RETRY_DELAY])

    def test_cache_over_3_days_server_down_runs_while_retrying(self):
        self.cache(3.5)
        (ok, msg, follow), calls = self.run_check(self.down)
        self.assertIs(ok, True); self.assertEqual(follow, L.WAIT); self.assertEqual(msg, L.CHECKING_MSG); self.assertEqual(calls, 2)

    def test_never_confirmed_server_down_waits(self):
        (ok, msg, follow), _ = self.run_check(self.down)
        self.assertIs(ok, L.WAITING); self.assertEqual(follow, L.WAIT); self.assertNotIn("Could not", msg)

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
        self.assertIs(ok, L.WAITING)          # rejected key is no longer trusted offline

    def test_background_refresh_rejection(self):
        self.cache(0.1)
        with mock.patch.object(L, "_ask", return_value={"valid": False, "message": "licence expired or not active"}):
            ok, msg, rejected, _ = L.refresh("KEY", URL, sleep=self.sleeps.append)
        self.assertFalse(ok); self.assertTrue(rejected); self.assertIsNone(L.load_cache())

    def test_background_refresh_down_inside_grace_keeps_running(self):
        self.cache(2)
        with mock.patch.object(L, "_ask", side_effect=self.down):
            ok, _, rejected, follow = L.refresh("KEY", URL, sleep=self.sleeps.append)
        self.assertIs(ok, True); self.assertFalse(rejected); self.assertIsNone(follow)
        self.cache(5)
        with mock.patch.object(L, "_ask", side_effect=self.down):
            ok, _, rejected, follow = L.refresh("KEY", URL, sleep=self.sleeps.append)
        self.assertIs(ok, True); self.assertEqual(follow, L.WAIT)

    def test_key_changed_ignores_cache(self):
        self.cache(0.1, key="OLD")
        (ok, msg, _), calls = self.run_check(self.down, key="NEW")
        self.assertIs(ok, L.WAITING); self.assertEqual(calls, 2)
        (ok, _, follow), calls = self.run_check(lambda *a: {"valid": True, "expires": FUTURE}, key="NEW")
        self.assertIs(ok, True); self.assertIsNone(follow); self.assertEqual(calls, 1)

    def test_cached_expiry_passed(self):
        self.cache(0.1, expires=PAST)
        (ok, _, follow), calls = self.run_check(self.down)      # no fast path: asks the server
        self.assertEqual(calls, 2)
        self.assertIs(ok, True); self.assertEqual(follow, L.WAIT)  # confirmed before: usable while retrying
        (ok, msg, _), _ = self.run_check(lambda *a: {"valid": False, "message": "licence expired or not active"})
        self.assertIs(ok, False); self.assertIn("expired", msg)

    def test_tampered_file_is_no_cache(self):
        self.cache(0.1, expires=PAST)
        p = C.path("licence.dat"); st = json.load(open(p))
        st["cache"]["expires"] = "2099-12-31"; json.dump(st, open(p, "w"))
        self.assertIsNone(L.load_cache())
        (ok, _, _), calls = self.run_check(self.down)
        self.assertIs(ok, L.WAITING); self.assertEqual(calls, 2)
        st["cache"]["sig"] = "0" * 64; json.dump(st, open(p, "w"))
        self.assertIsNone(L.load_cache())
        open(p, "w").write("not json"); self.assertIsNone(L.load_cache())

    def test_other_machine_is_no_cache(self):
        self.cache(0.1)
        with mock.patch.object(L, "machine_id", return_value="another-pc"):
            self.assertIsNone(L.load_cache())


class FakeClock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def sleep(self, s): self.t += s


class TestWaitForServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"SIGNALPILOT_HOME": self.tmp.name}); self.env.start()
        self.clock = FakeClock()

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def seq(self, answers):
        answers = list(answers); calls = []
        def ask(*a):
            calls.append(self.clock.t); x = answers.pop(0)
            if isinstance(x, Exception): raise x
            return x
        return ask, calls

    def test_asleep_then_awake_valid(self):
        down = OSError("asleep")
        ask, calls = self.seq([down, down, down, down, down, {"valid": True, "expires": FUTURE, "plan": "pro"}])
        hints = []
        with mock.patch.object(L, "_ask", side_effect=ask):
            r = L.wait_for_server("KEY", URL, status=hints.append, sleep=self.clock.sleep, clock=self.clock)
        self.assertEqual(r[0], True)
        self.assertEqual([round(b - a) for a, b in zip(calls, calls[1:])], [15, 30, 60, 120, 120])
        self.assertEqual(hints, [])                        # under 10 minutes: no hint
        self.assertIsNotNone(L.load_cache())               # now confirmed and cached

    def test_asleep_then_rejects(self):
        ask, _ = self.seq([OSError(), {"valid": False, "message": "licence revoked"}])
        with mock.patch.object(L, "_ask", side_effect=ask):
            r = L.wait_for_server("KEY", URL, sleep=self.clock.sleep, clock=self.clock)
        self.assertEqual(r[0], False); self.assertIn("revoked", r[1])

    def test_hint_after_10_minutes_once_and_keeps_going(self):
        ask, calls = self.seq([OSError()] * 9 + [{"valid": True, "expires": FUTURE}])
        hints = []
        with mock.patch.object(L, "_ask", side_effect=ask):
            r = L.wait_for_server("KEY", URL, status=hints.append, sleep=self.clock.sleep, clock=self.clock)
        self.assertTrue(r[0]); self.assertEqual(hints, [L.HINT_MSG]); self.assertEqual(len(calls), 10)

    def test_cancel_stops_waiting(self):
        n = []
        def cancelled(): n.append(1); return len(n) > 5
        with mock.patch.object(L, "_ask", side_effect=OSError()):
            self.assertIsNone(L.wait_for_server("KEY", URL, cancelled=cancelled, sleep=self.clock.sleep, clock=self.clock))


if __name__ == "__main__":
    unittest.main()
