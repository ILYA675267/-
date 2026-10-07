@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ============ УСТАНОВКА БОТА ============
echo.

python --version >nul 2>&1
if errorlevel 1 goto nopython

echo [1/3] Создаю отдельную папку для библиотек...
if not exist venv python -m venv venv
call venv\Scripts\activate.bat

echo [2/3] Скачиваю библиотеки. Это займёт 2-5 минут, просто жди...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 goto piperror

echo.
echo [3/3] Токен бота
if exist .env goto havetoken
echo Скопируй токен из сообщения @BotFather.
echo Вставь его сюда: нажми ПРАВУЮ кнопку мыши в этом окне, потом Enter.
set /p TOKEN=Токен: 
> .env echo BOT_TOKEN=%TOKEN%
>> .env echo ADMIN_ID=
echo Токен сохранён.
goto done

:havetoken
echo Токен уже был сохранён раньше - пропускаю.

:done
echo.
echo ============ ГОТОВО! ============
echo Теперь дважды щёлкни по файлу start.bat
echo.
pause
exit /b

:nopython
echo ОШИБКА: Python не найден.
echo Установи Python с сайта python.org и НЕ ЗАБУДЬ галочку "Add python.exe to PATH".
echo Потом снова запусти install.bat
pause
exit /b

:piperror
echo.
echo ОШИБКА при скачивании библиотек.
echo Сделай скриншот этого окна и пришли мне.
pause
exit /b
