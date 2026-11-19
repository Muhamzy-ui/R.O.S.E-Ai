@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo ERROR: venv not found. Run setup.bat first.
    pause
    exit /b 1
)
echo Closing any previous R.O.S.E instance...
"venv\Scripts\python.exe" main.py --kill-old
echo Starting latest R.O.S.E (PROJECT MIRROR)...
"venv\Scripts\python.exe" main.py %*
pause
