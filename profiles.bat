@echo off
REM Shows which Chrome profile is signed in to eBay.
setlocal
cd /d "%~dp0"
if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
echo.
nflcarddb profiles
echo.
pause
goto :eof

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.
pause
