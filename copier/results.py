"""Channel results: profit and loss of closed trades, grouped by the Telegram channel that sent the signal."""
import time
from . import config as C

PERIODS = (("Today", "today"), ("7 days", 7), ("30 days", 30), ("All", None))
OUT_ENTRIES = (1, 2, 3)      # DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT, DEAL_ENTRY_OUT_BY
UNKNOWN = "Not tagged"


def period_start(period, now=None):
    now = time.time() if now is None else now
    if period is None: return 0
    if period == "today":
        t = time.localtime(now); return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return now - int(period) * 86400


def ticket_map(store_data):
    """{ticket: chat_id} from the trade store: the permanent map plus every remembered signal."""
    out = {int(t): str(c) for t, c in (store_data.get("tickets") or {}).items()}
    for key, rec in (store_data.get("signals") or {}).items():
        chat = key.split(":", 1)[0]
        for t in rec.get("tickets") or []: out.setdefault(int(t), chat)
    return out


def _g(d, k, default=0):
    return d.get(k, default) if isinstance(d, dict) else getattr(d, k, default)


def aggregate(deals, tickets, names=None, since=0, magic=None, include_untagged=False):
    """deals: MT5 history deals (objects or dicts). A trade is one position; its result is
    profit + swap + commission + fee over all its deals. A trade counts once it has a closing deal,
    in the period of that closing deal. Returns rows sorted by net, best first."""
    names = names or {}
    pos = {}
    for d in deals:
        pid = int(_g(d, "position_id") or _g(d, "order"))
        p = pos.setdefault(pid, {"money": 0.0, "closed": 0, "chat": None, "magic": None})
        p["money"] += float(_g(d, "profit")) + float(_g(d, "swap")) + float(_g(d, "commission")) + float(_g(d, "fee"))
        if p["chat"] is None:
            p["chat"] = tickets.get(pid) or tickets.get(int(_g(d, "order"))) or tickets.get(int(_g(d, "ticket")))
        if int(_g(d, "entry")) in OUT_ENTRIES: p["closed"] = max(p["closed"], int(_g(d, "time")))
        if int(_g(d, "entry")) == 0: p["magic"] = _g(d, "magic")
    rows = {}
    for p in pos.values():
        if not p["closed"] or p["closed"] < since: continue
        chat = p["chat"]
        if chat is None:
            if not include_untagged or (magic is not None and p["magic"] != magic): continue
            chat = UNKNOWN
        r = rows.setdefault(chat, {"channel": chat, "name": names.get(chat, UNKNOWN if chat == UNKNOWN else chat),
                                   "profit": 0.0, "loss": 0.0, "net": 0.0, "trades": 0, "wins": 0})
        m = round(p["money"], 2)
        r["trades"] += 1; r["net"] += m
        if m > 0: r["profit"] += m; r["wins"] += 1
        elif m < 0: r["loss"] += m
    out = []
    for r in rows.values():
        r["net"] = round(r["net"], 2); r["profit"] = round(r["profit"], 2); r["loss"] = round(r["loss"], 2)
        r["win_rate"] = round(100.0 * r["wins"] / r["trades"], 1) if r["trades"] else 0.0
        out.append(r)
    return sorted(out, key=lambda r: r["net"], reverse=True)


def fetch(cfg, period, mt5=None, store_data=None, now=None):
    """Reads MT5 history and returns (rows, currency). MT5 must already be initialised."""
    if mt5 is None:
        from .executor import mt5
    if mt5 is None: raise RuntimeError("MetaTrader 5 is not available on this computer.")
    if store_data is None:
        from .store import Store
        store_data = Store().data
    now = time.time() if now is None else now
    since = period_start(period, now)
    from datetime import datetime
    # opening deals can be older than the period, so read a wider window for matching
    deals = mt5.history_deals_get(datetime.fromtimestamp(max(0, since - 90 * 86400)), datetime.fromtimestamp(now + 86400)) or []
    names = {k: v.get("name", k) for k, v in (cfg.get("channels") or {}).items()}
    ai = mt5.account_info()
    magic = int(cfg.get("magic_number", 0) or 0) or None
    rows = aggregate(deals, ticket_map(store_data), names, since, magic, include_untagged=magic is not None)
    return rows, getattr(ai, "currency", "") if ai else ""
