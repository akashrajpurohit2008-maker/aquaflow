@echo off
title AquaFlow Server
cd /d "%~dp0"
if not exist venv (
    echo Creating environment, first time only...
    python -m venv venv
)
call venv\Scripts\activate
pip install -q -r requirements.txt
start "AquaFlow Server" cmd /k "cd /d %~dp0 && venv\Scripts\activate && python app.py"
timeout /t 3 /nobreak >nul
start "" http://localhost:5000
echo AquaFlow is running at http://localhost:5000
echo Keep the "AquaFlow Server" window open. Close it to stop the site.
pause
