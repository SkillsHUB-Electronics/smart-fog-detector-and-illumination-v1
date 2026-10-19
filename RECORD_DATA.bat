@echo off
rem Record labelled training data from the Arduino Uno (USB)
cd /d "%~dp0"
python tools/record_data.py %*
pause
