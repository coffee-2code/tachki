@echo off
chcp 65001 >nul
cd /d "%~dp0"
call _setup.bat
.venv\Scripts\python -m carbot
pause
