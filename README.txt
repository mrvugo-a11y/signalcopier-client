=====================================================================
  SIGNALPILOT   v1.2.6   Telegram signals into MetaTrader 5 (Windows)
=====================================================================

WHAT IT DOES
  Reads signals from the Telegram channels you pick (using YOUR own
  Telegram account) and places the trades in your MetaTrader 5 terminal.
  It understands follow ups such as "close", "close half", "move SL to
  entry / BE", "TP1 hit", "cancel", "move SL to 1955" and edited signals.

INSTALL (customers)
  1. Install MetaTrader 5 from your broker and log in to your account.
     Click the "Algo Trading" button on the toolbar so it is GREEN.
  2. Run "SignalPilot Setup.exe" (from your account page) and click Next
     until it finishes. A desktop shortcut is created.
  3. SignalPilot opens a short setup:
       licence key  >  Telegram phone number  >  login code from Telegram
       (and your two step password if you have one)  >  tick your channels
       >  MetaTrader 5 is found automatically  >  Finish.
  4. Click "Start copying". The window shows every signal and trade.
     Click Settings to change lot size, take profits, per channel values...

  TIP: test on a DEMO account first.

YOUR FILES  (kept in %APPDATA%\SignalPilot, open with "Open data folder")
  config.json        your settings (the Settings window edits it for you)
  telegram.session   your Telegram login. Delete it to log out.
  trades.json        links each signal message to its MT5 tickets
  copier.log         the log      licence.dat   licence/trial state

PROBLEMS
  "Could not connect to MetaTrader 5"  open MT5 first; with several MT5
                       installs pick terminal64.exe in Settings, MetaTrader 5.
  "Order rejected ... 10027"  Algo Trading button is off in MT5.
  "your broker has no symbol called ..."  Settings, Rename symbols.
  Signal not copied    the message must contain BUY/SELL, a symbol and an
                       SL or TP.

---------------------------------------------------------------------
FOR THE OWNER (seller), one time
---------------------------------------------------------------------
  copier/app_credentials.py holds the built in Telegram app ID/hash
  (from my.telegram.org > API development tools) and LICENCE_URL
  (https://<your site>/api/licence). If the ID/hash are empty the setup
  asks the customer for their own. The GitHub workflow can also fill
  them from repository secrets TG_API_ID, TG_API_HASH, LICENCE_URL.
  Building: push to GitHub, Actions > "Build Windows installer" >
  Run workflow (artifact), or push a tag like v1.1.0 (GitHub Release).
  Local build on Windows: build_exe.bat. Tests: run_tests.bat.
  Running from source: install.bat then run.bat.
