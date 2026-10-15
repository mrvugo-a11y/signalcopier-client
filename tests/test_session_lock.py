"""One Telegram client per process, on one loop thread; channel list + copier share it; Start/Stop cycles."""
import asyncio, os, sqlite3, sys, tempfile, threading, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from copier import engine as E


class FakeClient:
    instances = []
    def __init__(self):
        self.connected = False; self.handlers = []; self.loop_threads = set(); self.disconnects = 0
        FakeClient.instances.append(self)
        class S:  # minimal session
            def save(_): return "SESSION"
        self.session = S()
    def _t(self): self.loop_threads.add(threading.current_thread().name)
    def is_connected(self): return self.connected
    async def connect(self): self._t(); self.connected = True
    async def disconnect(self): self._t(); self.connected = False; self.disconnects += 1
    async def start(self, **kw): self._t(); assert self.connected
    async def get_me(self):
        class Me: first_name = "Owner"; last_name = ""
        return Me()
    async def iter_dialogs(self):
        self._t()
        class D: is_channel = True; is_group = False; id = -1001; name = "Gold"
        yield D()
    def add_event_handler(self, h, e): self._t(); self.handlers.append((h, e))
    def remove_event_handler(self, h, e): self._t(); self.handlers.remove((h, e))


class FakeEx:
    def __init__(self, cfg, log): pass
    def connect(self): pass
    def shutdown(self): pass


class TestSingleClient(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(); os.environ["SIGNALPILOT_HOME"] = self.home
        FakeClient.instances = []; E.reset_service()
        self.saved = (E.make_client, E.Executor, E.C.load)
        E.make_client = lambda cfg: FakeClient()
        E.Executor = FakeEx
        E.C.load = lambda: {"channels": {"-1001": {"name": "Gold", "enabled": True}}}

    def tearDown(self):
        E.make_client, E.Executor, E.C.load = self.saved; E.reset_service()

    def _wait(self, cond, t=40):   # first telethon import can be slow on CI Windows runners
        end = time.time() + t
        while time.time() < end:
            if cond(): return True
            time.sleep(0.02)
        return False

    def test_channels_then_start_and_repeated_cycles(self):
        logs = []
        name, chans = E.login_and_list({}, None)          # app start: load channels
        self.assertEqual(chans, [(-1001, "Gold")])
        for i in range(5):                                  # Start / Stop cycles
            eng = E.Engine(logs.append, None); eng.start()
            self.assertTrue(self._wait(lambda: sum("Copying is ON" in l for l in logs) == i + 1), logs)
            c = FakeClient.instances[0]
            self.assertEqual(len(c.handlers), 2)
            eng.stop(); self.assertTrue(self._wait(lambda: not eng.running()))
            self.assertEqual(c.handlers, [])
            self.assertTrue(c.connected, "Stop must not disconnect the shared client")
            E.login_and_list({}, None)                      # channel list between runs
        self.assertEqual(len(FakeClient.instances), 1, "exactly one TelegramClient per process")
        self.assertEqual(FakeClient.instances[0].loop_threads, {"telegram"})
        self.assertFalse(any("Problem" in l for l in logs), logs)
        self.assertEqual(open(E.string_path()).read(), "SESSION")

    def test_channels_while_copying_no_stop_no_crash(self):
        logs = []
        eng = E.Engine(logs.append, None); eng.start()
        self.assertTrue(self._wait(lambda: any("Copying is ON" in l for l in logs)), logs)
        chans = E.safe_list_channels({}, None, logs.append)
        self.assertEqual(chans, [(-1001, "Gold")])
        self.assertTrue(eng.running(), "Channels must not stop copying")
        self.assertEqual(len(FakeClient.instances[0].handlers), 2)
        eng.stop(); self.assertTrue(self._wait(lambda: not eng.running()))
        self.assertEqual(E.safe_list_channels({}, None, logs.append), [(-1001, "Gold")])   # stop then list
        self.assertEqual(len(FakeClient.instances), 1)

    def test_channels_error_is_logged_not_raised(self):
        logs = []
        async def boom(self): raise RuntimeError("network gone")
        old = FakeClient.get_me; FakeClient.get_me = boom
        try: self.assertIsNone(E.safe_list_channels({}, None, logs.append))
        finally: FakeClient.get_me = old
        self.assertTrue(any("network gone" in l for l in logs))

    def test_reconnects_same_client_after_drop(self):
        E.login_and_list({}, None); FakeClient.instances[0].connected = False
        E.login_and_list({}, None)
        self.assertEqual(len(FakeClient.instances), 1); self.assertTrue(FakeClient.instances[0].connected)


class TestMigration(unittest.TestCase):
    def test_sqlite_session_migrated_without_relogin(self):
        from telethon.sessions import StringSession
        d = tempfile.mkdtemp(); f = os.path.join(d, "telegram.session")
        con = sqlite3.connect(f)
        con.execute("create table sessions (dc_id integer primary key, server_address text, port integer, auth_key blob, takeout_id integer)")
        con.execute("insert into sessions values (2, '149.154.167.51', 443, ?, null)", (bytes(range(256)),)); con.commit()
        con.execute("begin exclusive")                       # original is locked: migration must still work
        try:
            s = E.migrate_sqlite_session(f)
        finally: con.rollback(); con.close()
        ss = StringSession(s)
        self.assertEqual(ss.dc_id, 2); self.assertEqual(ss.port, 443); self.assertEqual(ss.auth_key.key, bytes(range(256)))

    def test_no_session(self):
        self.assertEqual(E.migrate_sqlite_session("/nonexistent/x.session"), "")


class TestHelpers(unittest.TestCase):
    def test_retry_then_success(self):
        calls = []
        def fn():
            calls.append(1)
            if len(calls) < 3: raise Exception("database is locked")
            return "ok"
        old = E.LOCKED_RETRIES; E.LOCKED_RETRIES = (0, 0, 0)
        try: self.assertEqual(asyncio.run(E._retry_locked(fn)), "ok")
        finally: E.LOCKED_RETRIES = old

    def test_friendly(self):
        self.assertIn("busy", E.friendly_error(Exception("database is locked")))
        self.assertEqual(E.friendly_error(Exception("x")), "x")


if __name__ == "__main__":
    unittest.main()
