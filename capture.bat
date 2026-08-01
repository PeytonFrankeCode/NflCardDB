@echo off
REM Fetches ONE eBay page with your signed-in Chrome and saves it, so the
REM page can be looked at when the collector reads nothing from it.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Capturing one eBay page
echo ==========================================================
echo.
echo Close Google Chrome completely first - including anything
echo still in the system tray.
echo.
pause
echo.

nflcarddb probe --query football_singles --chrome-profile --save-html data\html
echo.
echo ==========================================================
echo.
echo The page eBay sent was saved into the  data\html  folder.
echo.
echo SEND CLAUDE the newest file in that folder. That is the
echo real page, and it is what is needed to make the reader
echo understand eBay's current layout.
echo.

:END
pause
goto :eof

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.
pause
