"""Licence check stub.
- licence_url empty  -> offline TRIAL mode (TRIAL_DAYS from first run).
- licence_url set    -> POST {"key","machine","product"} to the URL (e.g. https://yoursite/api/licence), expects JSON {"valid": true, "expires": "..."}.
  A successful answer is cached, so the copier keeps running offline for GRACE_DAYS.
"""
import hashlib, json, os, platform, time, urllib.request, uuid
from . import config as C

TRIAL_DAYS = 7
GRACE_DAYS = 3

def machine_id():
    raw = f"{platform.node()}-{uuid.getnode()}-{platform.machine()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]

def _state():
    try:
        with open(C.path("licence.dat")) as f: return json.load(f)
    except Exception: return {}

def _write(st):
    with open(C.path("licence.dat"), "w") as f: json.dump(st, f)

def check(key, url):
    """Returns (ok: bool, message: str)."""
    st = _state()
    if not url:
        first = st.setdefault("trial_start", time.time()); _write(st)
        left = TRIAL_DAYS - (time.time() - first) / 86400
        if left > 0:
            return True, f"Trial mode: {left:.1f} days left"
        return False, "Trial expired. Please enter a licence key."
    if not key:
        return False, "Please enter your licence key."
    try:
        body = json.dumps({"key": key, "machine": machine_id(), "product": "tg-copier"}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        # free hosting can take about 50 s to wake up
        with urllib.request.urlopen(req, timeout=60) as r:
            ans = json.loads(r.read().decode())
        if ans.get("valid"):
            st["last_ok"] = time.time(); st["key"] = key; _write(st)
            return True, "Licence valid" + (f" until {ans['expires']}" if ans.get("expires") else "")
        return False, "Licence not accepted: " + str(ans.get("message", "rejected"))
    except Exception as e:
        if st.get("key") == key and time.time() - st.get("last_ok", 0) < GRACE_DAYS * 86400:
            return True, f"Licence server unreachable, offline grace active ({e})"
        return False, f"Could not verify licence: {e}"
