"""Automatic updates: asks the website (or GitHub as a fallback) for the latest version and installs it silently.
Customer data lives in %APPDATA%\\SignalPilot (see config.data_dir), so an update never touches settings, licence or login."""
import json, os, re, subprocess, sys, tempfile, urllib.request
from . import __version__, config as C

DEFAULT_SITE = "https://signalcopier-u6bo.onrender.com"
GITHUB_LATEST = "https://api.github.com/repos/mrvugo-a11y/signalpilot-client/releases/latest"
CHECK_EVERY_MS = 6 * 3600 * 1000


def site_base(cfg=None):
    url = C.licence_url(cfg or {})
    m = re.match(r"(https?://[^/]+)", url)
    return m.group(1) if m else DEFAULT_SITE


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:3]) or (0,)


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"SignalPilot/{__version__}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def latest(cfg=None):
    """Returns (version, download_url) or None."""
    base = site_base(cfg)
    try:
        d = _get_json(base + "/api/latest-version")
        if d.get("version"):
            return str(d["version"]).lstrip("v"), d.get("url") or base + "/download/SignalPilot-Setup.exe"
    except Exception:
        pass
    try:
        d = _get_json(GITHUB_LATEST)
        return str(d.get("tag_name", "")).lstrip("v"), base + "/download/SignalPilot-Setup.exe"
    except Exception:
        return None


def newer_available(cfg=None, current=__version__):
    r = latest(cfg)
    return r if r and vtuple(r[0]) > vtuple(current) else None


def download(url, progress=None):
    dest = os.path.join(tempfile.gettempdir(), "SignalPilot-Setup-update.exe"); tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": f"SignalPilot/{__version__}"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk: break
            f.write(chunk)
    if os.path.getsize(tmp) < 100_000:
        os.remove(tmp); raise RuntimeError("The download was incomplete. Please try again later.")
    os.replace(tmp, dest); return dest


def launch_installer(path):
    """Starts the Inno Setup installer silently a few seconds after this app has closed.
    The installer reopens SignalPilot when it finishes (see [Run] in installer.iss)."""
    if sys.platform != "win32":
        raise RuntimeError("Automatic install only works on Windows.")
    flags = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /CLOSEAPPLICATIONS"
    cmd = f'timeout /t 3 /nobreak >nul & "{path}" {flags}'
    subprocess.Popen(["cmd", "/c", cmd], creationflags=0x00000008 | 0x08000000, close_fds=True)  # DETACHED, NO_WINDOW
