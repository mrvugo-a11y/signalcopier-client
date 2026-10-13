# PyInstaller spec: builds dist\SignalPilot\SignalPilot.exe (one folder, windowed, no console).
# Build:  py -3.12 -m PyInstaller --noconfirm SignalPilot.spec
block_cipher = None
APP = "SignalPilot"   # keep in sync with copier/branding.py BRAND_NAME and installer.iss

a = Analysis(
    ["main.py"], pathex=["."], binaries=[],
    datas=[("README.txt", ".")],
    hiddenimports=["MetaTrader5", "pystray._win32", "PIL._tkinter_finder", "telethon"],
    excludes=["pytest"], cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP, console=False,
          icon="packaging/icon.ico" if __import__("os").path.exists("packaging/icon.ico") else None)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name=APP)
