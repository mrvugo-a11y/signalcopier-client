"""Safe MT5 history reads.

MetaTrader5.history_deals_get raises "returned a result with an exception set" when the date arguments
cannot be converted on the terminal side (for example datetime.fromtimestamp(0) = 1970-01-01 at local
time, which on a UTC+2 PC is before the epoch in the terminal's clock), and it can fail transiently right
after connecting. Every history read goes through here: sane dates, retries, a per-position fallback,
and mt5.last_error() in the error message. Callers must hold the engine's MT5 lock."""
import time
from datetime import datetime, timedelta

EARLIEST = datetime(2015, 1, 1)          # MT5 history never starts before this; far from the epoch edge


def _err(mt5):
    try: return mt5.last_error()
    except Exception: return None


def safe_range(since_ts=None, until_ts=None):
    """Naive datetimes, clamped into [2015-01-01, now + 2 days]."""
    hi = datetime.now() + timedelta(days=2)
    def conv(ts, default):
        if ts is None: return default
        try: d = datetime.fromtimestamp(float(ts))
        except (OverflowError, OSError, ValueError): return default
        return min(max(d, EARLIEST), hi)
    lo = conv(since_ts, EARLIEST)
    up = conv(until_ts, datetime.now() + timedelta(days=1))
    if up <= lo: up = lo + timedelta(days=1)
    return lo, up


def _call(fn, tries, delay, *a, **k):
    last = None
    for i in range(tries):
        try:
            r = fn(*a, **k)
            if r is not None: return list(r), None
            last = None
        except Exception as e:          # SystemError from the MT5 package
            last = e
        if i < tries - 1: time.sleep(delay)
    return None, last


def deals(mt5, since_ts=None, until_ts=None, positions=(), tries=3, delay=0.5, log=None):
    """All deals in the range. If the range query keeps failing, falls back to one query per known
    position ticket. Raises RuntimeError (with mt5.last_error()) only if nothing could be read."""
    lo, up = safe_range(since_ts, until_ts)
    got, exc = _call(mt5.history_deals_get, tries, delay, lo, up)
    if got is not None: return got
    first_err = _err(mt5)
    if exc is None and first_err and first_err[0] in (1, 0):     # RES_S_OK: genuinely no deals
        return []
    if log: log(f"MT5 history read failed ({exc or 'no data'}; last_error={first_err}), reading trade by trade")
    out, seen, ok = [], set(), False
    for p in sorted({int(t) for t in positions if t}):
        r, _ = _call(mt5.history_deals_get, 1, 0, position=p)
        if r is None: continue
        ok = True
        for d in r:
            key = getattr(d, "ticket", None) if not isinstance(d, dict) else d.get("ticket")
            if key in seen: continue
            seen.add(key); out.append(d)
    if ok or (not positions and exc is None): return out
    raise RuntimeError(f"MT5 history read failed: {exc or 'no data'} (MT5 error {first_err})")


def position_deals(mt5, ticket, tries=2, delay=0.3):
    r, _ = _call(mt5.history_deals_get, tries, delay, position=int(ticket))
    return r or []
