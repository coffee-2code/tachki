@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Первый запуск: ставлю зависимости и браузер, это 2-5 минут...
  python -m venv .venv
  .venv\Scripts\pip install -q -r requirements.txt
  .venv\Scripts\python -m playwright install chromium
)
if not exist .env copy .env.example .env >nul
