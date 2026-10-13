@echo off
REM Builds dist\SignalCopier\SignalCopier.exe and, if Inno Setup 6 is installed, "Output\SignalCopier Setup.exe"
cd /d "%~dp0"
py -3.12 -m pip install -r requirements.txt pyinstaller
py -3.12 -m PyInstaller --noconfirm SignalCopier.spec
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" installer.iss
echo Done.
pause
