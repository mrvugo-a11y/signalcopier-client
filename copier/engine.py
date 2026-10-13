"""Telegram listener: ties the parser, the ticket store and the MT5 executor together."""
import asyncio, threading
from . import config as C
from .parser import parse, parse_command, parse_update, Signal, Command
from .store import Store
from .executor import Executor


def session_path():
    return C.path("telegram")


# Only one TelegramClient may have the Telegram login (.session SQLite file) open at a time.
# Every client in the app is created through session_client(), which holds this lock until it disconnects.
SESSION_LOCK = threading.Lock()
SESSION_WAIT = 20          # seconds to wait for another part of the app to release the login
LOCKED_RETRIES = (0.5, 1, 2, 3)

LOCKED_MESSAGE = ("Your Telegram login file is busy (database is locked). Close any other SignalPilot window, "
                  "wait a few seconds and try again. If it keeps happening, restart your computer.")


def is_db_locked(e):
    return "database is locked" in str(e).lower()


def friendly_error(e):
    return LOCKED_MESSAGE if is_db_locked(e) else str(e)


def make_client(cfg):
    from telethon import TelegramClient
    api_id, api_hash = C.api_credentials(cfg)
    if not (api_id and api_hash):
        raise RuntimeError("Telegram app details are missing. Open Settings and run the setup again.")
    return TelegramClient(session_path(), api_id, api_hash)


async def _retry_locked(fn):
    """Runs fn() (sync or async), retrying a few times with a short backoff on 'database is locked'."""
    for delay in LOCKED_RETRIES + (None,):
        try:
            r = fn()
            return (await r) if asyncio.iscoroutine(r) else r
        except Exception as e:
            if delay is None or not is_db_locked(e): raise
            await asyncio.sleep(delay)


class session_client:
    """async with session_client(cfg) as client: the one shared login, always disconnected on exit."""
    def __init__(self, cfg): self.cfg = cfg; self.client = None; self.held = False

    async def __aenter__(self):
        loop = asyncio.get_running_loop()
        self.held = await loop.run_in_executor(None, lambda: SESSION_LOCK.acquire(timeout=SESSION_WAIT))
        if not self.held:
            raise RuntimeError("SignalPilot is still busy with Telegram (copier or channel list). Wait a few seconds and try again.")
        try:
            self.client = await _retry_locked(lambda: make_client(self.cfg))
        except BaseException:
            self.release(); raise
        return self.client

    async def __aexit__(self, *exc):
        try:
            if self.client is not None: await self.client.disconnect()
        except Exception:
            pass
        finally:
            try:
                if self.client is not None and getattr(self.client, "session", None): self.client.session.close()
            except Exception:
                pass
            self.release()

    def release(self):
        if self.held: self.held = False; SESSION_LOCK.release()


async def login(client, cfg, ask, phone=None):
    """Logs in, asking for the code and the two step password in dialogs when needed."""
    await _retry_locked(lambda: client.start(phone=lambda: phone or cfg.get("telegram_phone") or ask("Your Telegram phone number, with country code (for example +27821234567):", False),
                       code_callback=lambda: ask("Telegram just sent you a login code inside the Telegram app.\nType it here:", False),
                       password=lambda: ask("Your Telegram account has a two step password.\nType it here:", True)))


async def login_and_list(cfg, ask, phone=None):
    """Used by the setup wizard: logs in and returns (my_name, [(id, name), ...])."""
    async with session_client(cfg) as client:
        await login(client, cfg, ask, phone)
        me = await client.get_me()
        out = []
        async for d in client.iter_dialogs():
            if d.is_channel or d.is_group:
                out.append((d.id, d.name))
        name = " ".join(x for x in (getattr(me, "first_name", ""), getattr(me, "last_name", "")) if x)
        return name, out


async def list_channels(cfg, ask):
    return (await login_and_list(cfg, ask))[1]


class Engine:
    def __init__(self, log, ask, status=lambda kind, ok, text: None):
        self.log, self.ask, self.status = log, ask, status
        self.loop = None; self.client = None; self.thread = None
        self.mt5_lock = threading.Lock()

    # -- lifecycle --
    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True); self.thread.start()

    def stop(self):
        if self.loop and self.client:
            asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop)

    def reload_settings(self, cfg):
        """Apply new trade settings straight away (no restart). Channel list changes need a restart."""
        self.cfg = cfg
        if getattr(self, "ex", None): self.ex.cfg = cfg

    def channel_ids(self):
        return sorted(k for k, v in self.cfg.get("channels", {}).items() if v.get("enabled", True)) if getattr(self, "cfg", None) else []

    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def _run(self):
        self.loop = asyncio.new_event_loop(); asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception as e:
            self.log(f"Problem: {friendly_error(e)}")
        finally:
            try: self.ex.shutdown()
            except Exception: pass
            self.status("telegram", None, "Not connected"); self.status("mt5", None, "Not connected")
            self.log("Copying stopped.")

    async def _main(self):
        self.cfg = C.load()
        chans = {k: v for k, v in self.cfg.get("channels", {}).items() if v.get("enabled", True)}
        if not chans:
            raise RuntimeError("No channels are ticked. Click Channels and tick at least one.")
        self.store = Store()
        self.ex = Executor(self.cfg, self.log)
        try:
            self.ex.connect()
        except Exception as e:
            self.status("mt5", False, "Not connected"); raise RuntimeError(f"Could not connect to MetaTrader 5. Make sure it is open and logged in. ({e})")
        self.status("mt5", True, "Connected")
        from telethon import events
        async with session_client(self.cfg) as client:
            self.client = client
            try:
                await login(client, self.cfg, self.ask)
                self.status("telegram", True, "Connected")
                ids = [int(k) for k in chans]
                self.log("Copying is ON. Watching: " + ", ".join(v.get("name", k) for k, v in chans.items()))
                client.add_event_handler(self._on_new, events.NewMessage(chats=ids))
                client.add_event_handler(self._on_edit, events.MessageEdited(chats=ids))
                await client.run_until_disconnected()
            finally:
                self.client = None

    # -- handlers --
    def _st(self, chat_id):
        return C.channel_settings(self.cfg, chat_id)

    def _name(self, chat_id):
        return self.cfg["channels"].get(str(chat_id), {}).get("name", str(chat_id))

    def _open(self, chat_id, msg_id, sig, st):
        with self.mt5_lock:
            tickets = self.ex.open_signal(sig, st)
        if tickets:
            self.store.put(chat_id, msg_id, {"symbol": sig.symbol, "side": sig.side, "sl": sig.sl,
                                             "tps": sig.tps, "tickets": tickets})

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
