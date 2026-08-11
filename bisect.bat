@echo off
REM Finds which part of the search eBay is refusing. Seven requests, ~30 sec.
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Which part of the search is being refused
echo ==========================================================
echo.
echo This asks eBay for the same search seven times, adding one
echo setting each time, and reports the first one it refuses.
echo That names the thing to change.
echo.

nflcarddb bisect

echo.
echo The report above was also saved to  bisect-report.txt
echo in this folder. Send it to Claude.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
