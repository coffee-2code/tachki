@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist .venv\ready.txt goto env

echo ================================================
echo  Первая настройка: библиотеки и браузер, 2-5 минут
echo ================================================
python --version >nul 2>&1
if errorlevel 1 (
  echo.
  echo Не найден Python. Установите его с python.org и поставьте галочку "Add python.exe to PATH".
  pause
  exit /b 1
)
python -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else 1)"
if errorlevel 1 (
  echo.
  python --version
  echo Нужен Python от 3.10 до 3.14. Лучше всего 3.12 или 3.13 с python.org.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo ОШИБКА при установке библиотек. Сфотографируйте текст выше и пришлите.
  pause
  exit /b 1
)
.venv\Scripts\python -m playwright install chromium
if errorlevel 1 (
  echo.
  echo Внимание: встроенный браузер не скачался. Если у вас установлен Google Chrome - будет использован он.
)
echo ok> .venv\ready.txt
echo Готово.

:env
if not exist .env copy .env.example .env >nul
exit /b 0
