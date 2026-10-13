"""Remembers which MT5 tickets belong to which Telegram message (survives restarts)."""
import json, os, threading
from . import config as C

class Store:
    def __init__(self, path=None):
        path = path or C.path("trades.json")
        self.path, self.lock = path, threading.Lock()
        try:
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        except Exception:
            self.data = {"signals": {}, "last": {}}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)

    def put(self, chat_id, msg_id, record):
        """record = {symbol, side, entry, sl, tickets:[...]}"""
        with self.lock:
            self.data["signals"][f"{chat_id}:{msg_id}"] = record
            tm = self.data.setdefault("tickets", {})     # permanent ticket to channel map, for Channel results
            for t in record.get("tickets") or []: tm[str(t)] = str(chat_id)
            self.data["last"][str(chat_id)] = msg_id
            if len(self.data["signals"]) > 2000:
                for k in list(self.data["signals"])[:500]:
                    del self.data["signals"][k]
            self._save()

    def get(self, chat_id, msg_id):
        return self.data["signals"].get(f"{chat_id}:{msg_id}")

    def last_id(self, chat_id):
        return self.data["last"].get(str(chat_id))

    def update(self, chat_id, msg_id, **kw):
        with self.lock:
            r = self.data["signals"].get(f"{chat_id}:{msg_id}")
            if r is not None:
                r.update(kw); self._save()
