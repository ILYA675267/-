@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist venv goto noinstall
call venv\Scripts\activate.bat
echo Бот запускается... Не закрывай это окно, пока бот нужен.
python bot.py
pause
exit /b

:noinstall
echo Сначала запусти install.bat
pause
