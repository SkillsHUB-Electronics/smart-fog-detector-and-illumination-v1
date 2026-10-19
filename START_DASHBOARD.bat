@echo off
rem ==================================================================
rem  Fog Lane Light - dashboard (Arduino Uno over USB serial)
rem   Starts the dashboard server and opens it in the browser.
rem   If exactly one Arduino-like COM port is plugged in it connects
rem   automatically; otherwise choose the port (or the simulator) on the page.
rem   Close the Arduino Serial Monitor and RECORD_DATA.bat first.
rem   Extra options are passed on, e.g.:  START_DASHBOARD.bat --sim
rem                                       START_DASHBOARD.bat --port COM7
rem ==================================================================
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

set "PY="
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto havepy
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto havepy
echo [ERROR] Python 3 was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
goto end

:havepy
echo Starting the dashboard...  (close this window or press Ctrl+C to stop it)
echo.
%PY% "dashboard\server.py" --auto --open %*

:end
echo.
pause
