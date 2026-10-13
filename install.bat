@echo off
cd /d "%~dp0"
py -3.12 -m pip install --upgrade pip
py -3.12 -m pip install -r requirements.txt
echo.
echo Done. Now double-click run.bat
pause
