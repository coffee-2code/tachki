@echo off
chcp 65001 >nul
cd /d "%~dp0"
call _setup.bat
if "%~1"=="" (
  echo Перетащите Excel-файл на этот значок.
  pause
  exit /b
)
.venv\Scripts\python -m carbot.cli "%~1"
pause
