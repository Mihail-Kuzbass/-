@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher 'py' not found. Install Python 3.11 or newer from python.org.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto :fail
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail
python -m compileall -q app tests
if errorlevel 1 goto :fail
python -m pytest -q
if errorlevel 1 goto :fail
python -m PyInstaller --noconfirm --clean --windowed --name TrafficSignalStudio --collect-all PySide6 --hidden-import requests app\main.py
if errorlevel 1 goto :fail
echo.
echo Build complete: dist\TrafficSignalStudio\TrafficSignalStudio.exe
 echo Note: this is a folder-based build. Copy the whole dist\TrafficSignalStudio folder.
pause
exit /b 0
:fail
echo Build failed. Review the error above.
pause
exit /b 1
