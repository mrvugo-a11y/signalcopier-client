"""The shared Telegram login: one client at a time, retry on 'database is locked', friendly message."""
import asyncio, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier import engine as E


class FakeClient:
    def __init__(self): self.disconnected = False
    async def disconnect(self): self.disconnected = True


class TestSessionLock(unittest.TestCase):
    def test_retry_then_success(self):
        calls = []
        def fn():
            calls.append(1)
            if len(calls) < 3: raise Exception("database is locked")
            return "ok"
        old = E.LOCKED_RETRIES; E.LOCKED_RETRIES = (0, 0, 0)
        try: self.assertEqual(asyncio.run(E._retry_locked(fn)), "ok")
        finally: E.LOCKED_RETRIES = old
        self.assertEqual(len(calls), 3)

    def test_other_errors_not_retried(self):
        calls = []
        def fn(): calls.append(1); raise ValueError("boom")
        with self.assertRaises(ValueError): asyncio.run(E._retry_locked(fn))
        self.assertEqual(len(calls), 1)

    def test_one_client_at_a_time_and_released(self):
        made = []
        orig = E.make_client; E.make_client = lambda cfg: made.append(FakeClient()) or made[-1]
        try:
            async def run():
                async with E.session_client({}) as c:
                    self.assertTrue(E.SESSION_LOCK.locked())
                return c
            c = asyncio.run(run())
            self.assertTrue(c.disconnected); self.assertFalse(E.SESSION_LOCK.locked())
            E.SESSION_LOCK.acquire(); old = E.SESSION_WAIT; E.SESSION_WAIT = 0.1
            try:
                with self.assertRaises(RuntimeError): asyncio.run(run())
            finally: E.SESSION_LOCK.release(); E.SESSION_WAIT = old
        finally: E.make_client = orig

    def test_friendly(self):
        self.assertIn("busy", E.friendly_error(Exception("database is locked")))
        self.assertEqual(E.friendly_error(Exception("x")), "x")


if __name__ == "__main__":
    unittest.main()
