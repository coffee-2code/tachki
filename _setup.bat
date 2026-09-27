@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Первый запуск: ставлю зависимости, это 1-2 минуты...
  python -m venv .venv
  .venv\Scripts\pip install -q -r requirements.txt
)
if not exist .env copy .env.example .env >nul
