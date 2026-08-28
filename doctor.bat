@echo off
REM Double-click this to find out exactly what eBay is doing.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Testing every method against eBay
echo ==========================================================
echo.
echo This tries each method once and reports what came back.
echo It takes about a minute.
echo.

python -m nflcarddb doctor > doctor-report.txt 2>&1
type doctor-report.txt

echo.
echo ==========================================================
echo.
echo The report above was also saved to  doctor-report.txt
echo in this folder.
echo.
echo SEND CLAUDE:
echo   1. doctor-report.txt
echo   2. the files in the  data\html  folder
echo.
echo Those show exactly what eBay returned, which is what is
echo needed to fix this.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
