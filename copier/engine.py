"""Telegram listener: ties the parser, the ticket store and the MT5 executor together."""
import asyncio, os, threading
from . import config as C
from .parser import parse, parse_command, parse_update, Signal, Command
from .store import Store
from .executor import Executor


def session_path():
    return C.path("telegram")


# Exactly ONE TelegramClient per process, living on ONE asyncio loop in ONE dedicated thread
# (TelegramService). The setup wizard, the channel list and the copier all reuse it through
# run_coroutine_threadsafe. The login is kept as a StringSession in memory and saved to
# "telegram.string", so no SQLite file is held open at all (no more "database is locked").
# An existing "telegram.session" (SQLite) login is migrated once, read from a copy, without re-login.
LOCKED_RETRIES = (0.5, 1, 2, 3)

LOCKED_MESSAGE = ("Your Telegram login file is busy (database is locked). Close any other SignalPilot window, "
                  "wait a few seconds and try again. If it keeps happening, restart your computer.")


def is_db_locked(e):
    return "database is locked" in str(e).lower()


def friendly_error(e):
    return LOCKED_MESSAGE if is_db_locked(e) else str(e)


def string_path():
    return C.path("telegram.string")


def load_session_string():
    """The saved login as a StringSession string. Migrates an old telegram.session file once."""
    try:
        with open(string_path(), encoding="utf-8") as f:
            s = f.read().strip()
        if s: return s
    except OSError:
        pass
    s = migrate_sqlite_session(session_path() + ".session")
    if s:
        try: save_session_string(s)
        except OSError: pass
    return s


def save_session_string(s):
    if not s: return
    tmp = string_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: f.write(s)
    os.replace(tmp, string_path())


def migrate_sqlite_session(file):
    """Reads dc + auth key out of a Telethon .session (SQLite) file, from a temporary copy so a lock
    on the original cannot block us. Returns a StringSession string, or "" when there is no login."""
    if not os.path.exists(file): return ""
    import sqlite3, shutil, tempfile
    d = tempfile.mkdtemp()
    try:
        cp = os.path.join(d, "s.session"); shutil.copyfile(file, cp)
        con = sqlite3.connect(cp)
        try: row = con.execute("select dc_id, server_address, port, auth_key from sessions").fetchone()
        finally: con.close()
    except Exception:
        return ""
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if not row or not row[3]: return ""
    from telethon.sessions import StringSession
    from telethon.crypto import AuthKey
    ss = StringSession(); ss.set_dc(row[0], row[1], row[2]); ss.auth_key = AuthKey(data=row[3])
    return ss.save()


def make_client(cfg):
    """Only ever called by TelegramService, once per process."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    api_id, api_hash = C.api_credentials(cfg)
    if not (api_id and api_hash):
        raise RuntimeError("Telegram app details are missing. Open Settings and run the setup again.")
    return TelegramClient(StringSession(load_session_string()), api_id, api_hash)


async def _retry_locked(fn):
    """Runs fn() (sync or async), retrying a few times with a short backoff on 'database is locked'."""
    for delay in LOCKED_RETRIES + (None,):
        try:
            r = fn()
            return (await r) if asyncio.iscoroutine(r) else r
        except Exception as e:
            if delay is None or not is_db_locked(e): raise
            await asyncio.sleep(delay)


class TelegramService:
    """Owns the single client and the single loop thread. Use service() to get it."""
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="telegram", daemon=True)
        self.thread.start()
        self.client = None; self.created = 0

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def run(self, coro, timeout=None):
        """Run a coroutine on the Telegram loop from any other thread and wait for the result."""
        if threading.current_thread() is self.thread:
            raise RuntimeError("TelegramService.run called from the Telegram thread")
        return self.submit(coro).result(timeout)

    async def get_client(self, cfg):
        """The one client, created on first use and (re)connected if needed. Runs on the loop."""
        if self.client is None:
            self.client = make_client(cfg); self.created += 1
        if not self.client.is_connected():
            await self.client.connect()
        return self.client

    def save(self):
        try:
            if self.client is not None: save_session_string(self.client.session.save())
        except Exception:
            pass

    def shutdown(self):
        if self.client is not None:
            try: self.run(self.client.disconnect(), 10)
            except Exception: pass
            self.save()
        self.loop.call_soon_threadsafe(self.loop.stop)


_SERVICE = None
_SERVICE_LOCK = threading.Lock()


def service():
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None: _SERVICE = TelegramService()
        return _SERVICE


def reset_service():
    """Tests only."""
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is not None: _SERVICE.loop.call_soon_threadsafe(_SERVICE.loop.stop)
        _SERVICE = None


async def login(client, cfg, ask, phone=None):
    """Logs in, asking for the code and the two step password in dialogs when needed."""
    await _retry_locked(lambda: client.start(phone=lambda: phone or cfg.get("telegram_phone") or ask("Your Telegram phone number, with country code (for example +27821234567):", False),
                       code_callback=lambda: ask("Telegram just sent you a login code inside the Telegram app.\nType it here:", False),
                       password=lambda: ask("Your Telegram account has a two step password.\nType it here:", True)))
    service().save()


async def _login_and_list(cfg, ask, phone=None):
    client = await service().get_client(cfg)
    await login(client, cfg, ask, phone)
    me = await client.get_me()
    out = []
    async for d in client.iter_dialogs():
        if d.is_channel or d.is_group:
            out.append((d.id, d.name))
    name = " ".join(x for x in (getattr(me, "first_name", ""), getattr(me, "last_name", "")) if x)
    return name, out


def login_and_list(cfg, ask, phone=None, timeout=None):
    """Blocking (call from a worker thread). Logs in on the shared client and returns (my_name, [(id, name), ...])."""
    return service().run(_login_and_list(cfg, ask, phone), timeout)


def safe_list_channels(cfg, ask, log, timeout=300):
    """Channel list for the Channels button. Works while copying (shared client). Never raises: logs and returns None."""
    try:
        return login_and_list(cfg, ask, timeout=timeout)[1]
    except BaseException as e:
        try: log(f"Could not load channels: {friendly_error(e) or type(e).__name__}")
        except Exception: pass
        return None


def _title(ent):
    t = getattr(ent, "title", None)
    if not t:
        t = " ".join(x for x in (getattr(ent, "first_name", None), getattr(ent, "last_name", None)) if x)
    if t: return t
    u = getattr(ent, "username", None)
    return f"@{u}" if u else None


async def _resolve_titles(cfg, ids):
    client = await service().get_client(cfg)
    if not await client.is_user_authorized(): return {}
    out, missing = {}, []
    for i in ids:
        try:
            t = _title(await client.get_entity(int(i)))
            if t: out[str(i)] = t
            else: missing.append(str(i))
        except Exception:
            missing.append(str(i))
    if missing:                                    # not in the session cache: look through the dialog list
        want = set(missing)
        async for d in client.iter_dialogs():
            k = str(d.id)
            if k in want:
                t = d.name or _title(d.entity)
                if t: out[k] = t
                want.discard(k)
                if not want: break
    return out


def resolve_titles(cfg, ids, timeout=60):
    """Blocking (worker thread only). {chat_id: title} for the ids Telegram knows. Never asks to log in, never raises."""
    ids = [str(i) for i in ids if str(i).lstrip("-").isdigit()]
    if not ids: return {}
    try:
        return service().run(_resolve_titles(cfg, ids), timeout) or {}
    except BaseException:
        return {}


def list_channels(cfg, ask):
    return login_and_list(cfg, ask)[1]


class Engine:
    def __init__(self, log, ask, status=lambda kind, ok, text: None):
        self.log, self.ask, self.status = log, ask, status
        self.loop = None; self.client = None; self.thread = None
        self.mt5_lock = threading.Lock()

    # -- lifecycle --
    def start(self):
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True); self.thread.start()

    def stop(self):
        """Stops copying. The shared Telegram client stays connected so the next Start or channel list just works."""
        ev = getattr(self, "stop_event", None)
        if ev: ev.set()

    def reload_settings(self, cfg):
        """Apply new trade settings straight away (no restart). Channel list changes need a restart."""
        self.cfg = cfg
        if getattr(self, "ex", None): self.ex.cfg = cfg

    def channel_ids(self):
        return sorted(k for k, v in self.cfg.get("channels", {}).items() if v.get("enabled", True)) if getattr(self, "cfg", None) else []

    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def _run(self):
        try:
            self._main()
        except Exception as e:
            self.log(f"Problem: {friendly_error(e)}")
        finally:
            try: self.ex.shutdown()
            except Exception: pass
            self.status("telegram", None, "Not connected"); self.status("mt5", None, "Not connected")
            self.log("Copying stopped.")

    def _main(self):
        self.cfg = C.load()
        chans = {k: v for k, v in self.cfg.get("channels", {}).items() if v.get("enabled", True)}
        if not chans:
            raise RuntimeError("No channels are ticked. Click Channels and tick at least one.")
        self.store = Store()
        try:                                       # remember every known channel title for Channel results
            self.store.set_names({k: v.get("name") for k, v in self.cfg.get("channels", {}).items()
                                  if v.get("name") and v.get("name") != str(k)})
        except Exception: pass
        self.ex = Executor(self.cfg, self.log)
        try:
            self.ex.connect()
        except Exception as e:
            self.status("mt5", False, "Not connected"); raise RuntimeError(f"Could not connect to MetaTrader 5. Make sure it is open and logged in. ({e})")
        self.status("mt5", True, "Connected")
        if self.stop_event.is_set(): return
        from telethon import events
        svc = service(); ids = [int(k) for k in chans]
        handlers = ((self._on_new, events.NewMessage(chats=ids)), (self._on_edit, events.MessageEdited(chats=ids)))

        async def attach():
            client = await svc.get_client(self.cfg)
            await login(client, self.cfg, self.ask)
            for h, e in handlers: client.add_event_handler(h, e)
            return client

        async def detach(client):
            for h, e in handlers: client.remove_event_handler(h, e)

        self.client = svc.run(attach()); self.loop = svc.loop
        try:
            self.status("telegram", True, "Connected")
            self.log("Copying is ON. Watching: " + ", ".join(v.get("name", k) for k, v in chans.items()))
            while not self.stop_event.wait(5):
                self._check_break_even()
                if not self.client.is_connected():      # network dropped: reconnect the same client
                    try: svc.run(svc.get_client(self.cfg), 60)
                    except Exception as e: self.log(f"Telegram reconnect failed, retrying: {friendly_error(e)}")
        finally:
            try: svc.run(detach(self.client), 10)
            except Exception: pass
            svc.save(); self.client = None

    # -- handlers --
    def _st(self, chat_id):
        return C.channel_settings(self.cfg, chat_id)

    def _name(self, chat_id):
        return self.cfg["channels"].get(str(chat_id), {}).get("name", str(chat_id))

    def _open(self, chat_id, msg_id, sig, st):
        tid = getattr(sig, "trade_id", None)
        if tid and self.store.has_trade(chat_id, tid):
            self.log(f"[{self._name(chat_id)}] trade #{tid} was already copied, ignoring repost"); return
        with self.mt5_lock:
            tickets = self.ex.open_signal(sig, st)
        if tickets:
            self.store.put(chat_id, msg_id, {"symbol": sig.symbol, "side": sig.side, "sl": sig.sl,
                                             "tps": sig.tps, "tickets": tickets,
                                             "tp1": getattr(self.ex, "last_tp1", None),
                                             "channel_name": self._name(chat_id)})
            self.store.mark_trade(chat_id, tid, msg_id)

    def _check_break_even(self):
        """Break even after TP1 (only when the customer ticked it for that channel)."""
        if not hasattr(self, "_be_warned"): self._be_warned = set()
        try:
            for key, rec in list(self.store.data.get("signals", {}).items()):
                if rec.get("be_done") or not rec.get("tickets"): continue
                chat_id, msg_id = key.rsplit(":", 1)
                if not C.channel_settings(self.cfg, chat_id).get("be_after_tp1", False): continue
                with self.mt5_lock:
                    done = self.ex.check_break_even(rec, self._st(chat_id), self._be_warned)
                if done: self.store.update(chat_id, msg_id, be_done=True)
        except Exception as e:
            self.log(f"Break even check failed: {e}")

    def _apply(self, chat_id, msg_id, cmd, st):
        rec = self.store.get(chat_id, msg_id) if msg_id else None
        if not rec:
            self.log(f"[{self._name(chat_id)}] {cmd.action.replace('_', ' ').lower()}: no copied trades belong to that signal, nothing to do"); return
        self.log(f"[{self._name(chat_id)}] instruction {cmd.action.replace('_', ' ').lower()} for trades {', '.join(map(str, rec['tickets']))}")
        with self.mt5_lock:
            self.ex.apply(cmd, rec["tickets"], st)

    async def _on_new(self, ev):
        try:
            text, chat, st = ev.raw_text or "", ev.chat_id, self._st(ev.chat_id)
            if not st.get("enabled", True): return
            parsed = parse(text)
            reply_id = ev.message.reply_to_msg_id
            if isinstance(parsed, Signal):
                self.log(f"[{self._name(chat)}] SIGNAL received: {parsed.side} {parsed.symbol}"
                         f"{' ' + parsed.order_type if parsed.order_type and parsed.order_type.upper() != 'MARKET' else ''}, entry {parsed.entry or 'now'}"
                         f"{' to ' + str(parsed.entry_high) if parsed.entry_high else ''}, stop loss {parsed.sl or 'none'}, take profits {', '.join(map(str, parsed.tps)) or 'none'}")
                self._open(chat, ev.id, parsed, st); return
            if reply_id:
                cmd = parsed if isinstance(parsed, Command) else parse_update(text)
                if cmd: self._apply(chat, reply_id, cmd, st)
                return
            if isinstance(parsed, Command) and st.get("commands_without_reply_apply_to_last", True):
                self._apply(chat, self.store.last_id(chat), parsed, st)
        except Exception as e:
            self.log(f"Could not handle a message: {e}")

    async def _on_edit(self, ev):
        try:
            chat, st = ev.chat_id, self._st(ev.chat_id)
            if not st.get("follow_edits", True) or not st.get("enabled", True): return
            text = ev.raw_text or ""
            rec = self.store.get(chat, ev.id)
            parsed = parse(text)
            if rec is None:
                if isinstance(parsed, Signal):      # signal completed by an edit
                    self.log(f"[{self._name(chat)}] edited message is now a signal")
                    self._open(chat, ev.id, parsed, st)
                return
            if isinstance(parsed, Signal):
                if parsed.sl != rec.get("sl") or parsed.tps != rec.get("tps"):
                    self.log(f"[{self._name(chat)}] signal was edited: stop loss {parsed.sl}, take profits {parsed.tps}")
                    self._apply(chat, ev.id, Command("MODIFY", sl=parsed.sl, tps=parsed.tps), st)
                    self.store.update(chat, ev.id, sl=parsed.sl, tps=parsed.tps)
            elif isinstance(parsed, Command):
                self._apply(chat, ev.id, parsed, st)
        except Exception as e:
            self.log(f"Could not handle an edited message: {e}")
