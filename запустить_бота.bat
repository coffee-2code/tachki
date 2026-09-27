@echo off
chcp 65001 >nul
cd /d "%~dp0"
call _setup.bat
if errorlevel 1 exit /b 1
.venv\Scripts\python -m carbot
pause
