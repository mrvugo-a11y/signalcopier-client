"""Licence check.
- licence_url empty  -> offline TRIAL mode (TRIAL_DAYS from first run).
- licence_url set    -> POST {"key","machine","product"} to the URL (e.g. https://yoursite/api/licence), expects JSON
  {"valid": true, "expires": "YYYY-MM-DD", "plan": "..."} or {"valid": false, "message": "..."}.

The last confirmed answer is cached in licence.dat (signed with an HMAC tied to this computer):
- cache under CACHE_HOURS old and its expiry not passed -> OK at once, no network call (the app then refreshes quietly).
- otherwise ask the server (60 s timeout, one retry after 10 s when it cannot be reached).
- server unreachable -> still OK while the last confirmation is under GRACE_DAYS old and its expiry has not passed.
- an explicit rejection from the server always blocks and clears the cache.
"""
import hashlib, hmac, json, os, platform, time, urllib.error, urllib.request, uuid
from datetime import date, datetime
from . import config as C

TRIAL_DAYS = 7
GRACE_DAYS = 3
CACHE_HOURS = 24
RECHECK_HOURS = 12
TIMEOUT = 60          # free hosting can take about 50 s to wake up
RETRY_DELAY = 10
_SECRET = b"SignalPilot/licence-cache/v1/7f3a91c2e4b8"
UNREACHABLE_MSG = "Could not reach the licence server. Check your internet and try again."

OK, REJECTED, UNREACHABLE = "ok", "rejected", "unreachable"


def machine_id():
    raw = f"{platform.node()}-{uuid.getnode()}-{platform.machine()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _state():
    try:
        with open(C.path("licence.dat")) as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception: return {}


def _write(st):
    with open(C.path("licence.dat"), "w") as f: json.dump(st, f)


# ---- signed cache
_FIELDS = ("key", "machine", "last_ok", "expires", "plan")

def _sig(c, machine):
    secret = hashlib.sha256(_SECRET + machine.encode()).digest()
    msg = json.dumps([c.get(k) for k in _FIELDS], separators=(",", ":")).encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def load_cache():
    """The cached confirmation, or None when missing, for another computer or tampered with."""
    c = _state().get("cache")
    if not isinstance(c, dict): return None
    m = machine_id()
    if c.get("machine") != m or not hmac.compare_digest(str(c.get("sig", "")), _sig(c, m)): return None
    return c


def save_cache(key, expires, plan, now=None):
    c = {"key": key, "machine": machine_id(), "last_ok": float(now if now is not None else time.time()),
         "expires": expires or "", "plan": plan or ""}
    c["sig"] = _sig(c, c["machine"])
    st = _state(); st["cache"] = c
    for old in ("last_ok", "key"): st.pop(old, None)   # pre 1.2.4 unsigned fields
    _write(st)


def clear_cache():
    st = _state()
    if any(k in st for k in ("cache", "last_ok", "key")):
        for k in ("cache", "last_ok", "key"): st.pop(k, None)
        _write(st)


def _expired(expires, now):
    if not expires: return False
    try: d = datetime.strptime(str(expires)[:10], "%Y-%m-%d").date()
    except ValueError: return False
    return date.fromtimestamp(now) > d


def _usable(c, key, now, max_age):
    return bool(c and c.get("key") == key and 0 <= now - float(c.get("last_ok", 0)) < max_age
                and not _expired(c.get("expires"), now))


def _valid_msg(expires, extra=""):
    return "Licence valid" + (f" until {expires}" if expires else "") + extra


# ---- server
def _ask(key, url):
    body = json.dumps({"key": key, "machine": machine_id(), "product": "tg-copier"}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def server_check(key, url, sleep=time.sleep):
    """Asks the server. Returns (OK | REJECTED | UNREACHABLE, message). Updates or clears the cache."""
    ans = None
    for attempt in range(2):
        try:
            ans = _ask(key, url)
            if not isinstance(ans, dict) or "valid" not in ans: raise ValueError("unexpected answer")
            break
        except Exception:
            ans = None
            if attempt == 0: sleep(RETRY_DELAY)
    if ans is None:
        return UNREACHABLE, UNREACHABLE_MSG
    if ans.get("valid"):
        save_cache(key, ans.get("expires"), ans.get("plan"))
        return OK, _valid_msg(ans.get("expires"))
    clear_cache()
    return REJECTED, "Licence not accepted: " + str(ans.get("message", "rejected"))


def check_full(key, url, now=None, sleep=time.sleep):
    """Returns (ok, message, from_cache). from_cache=True means the caller should refresh in the background."""
    st = _state()
    if not url:
        first = st.setdefault("trial_start", time.time()); _write(st)
        left = TRIAL_DAYS - (time.time() - first) / 86400
        if left > 0:
            return True, f"Trial mode: {left:.1f} days left", False
        return False, "Trial expired. Please enter a licence key.", False
    if not key:
        return False, "Please enter your licence key.", False
    now = time.time() if now is None else now
    c = load_cache()
    if _usable(c, key, now, CACHE_HOURS * 3600):
        return True, _valid_msg(c.get("expires")), True
    status, msg = server_check(key, url, sleep)
    if status == OK: return True, msg, False
    if status == REJECTED: return False, msg, False
    return offline_verdict(key, now)


def offline_verdict(key, now=None):
    """Used when the server cannot be reached: (ok, message, False)."""
    now = time.time() if now is None else now
    c = load_cache()
    if _usable(c, key, now, GRACE_DAYS * 86400):
        return True, _valid_msg(c.get("expires"), " (licence server unreachable, offline grace active)"), False
    return False, UNREACHABLE_MSG, False


def refresh(key, url, sleep=time.sleep):
    """Background recheck. Returns (still_ok, message, rejected)."""
    if not url or not key:
        ok, msg, _ = check_full(key, url, sleep=sleep); return ok, msg, False
    status, msg = server_check(key, url, sleep)
    if status == OK: return True, msg, False
    if status == REJECTED: return False, msg, True
    ok, msg, _ = offline_verdict(key); return ok, msg, False


def check(key, url):
    """Returns (ok: bool, message: str)."""
    return check_full(key, url)[:2]
