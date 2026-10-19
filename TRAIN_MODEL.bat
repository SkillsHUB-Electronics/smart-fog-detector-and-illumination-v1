@echo off
rem Train the AI model, compare the sensors, install weights into the firmware
cd /d "%~dp0"
python tools/train_model.py --compare --install %*
pause
