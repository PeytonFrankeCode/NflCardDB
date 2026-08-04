@echo off
REM One-time: creates the online database, uploads your sales, and deploys
REM the API. Prints the two values to paste into your website.
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Setting up your online API
echo ==========================================================
echo.
echo This does everything in one go:
echo   - signs you in to Cloudflare (a browser window opens)
echo   - creates the online database
echo   - uploads the sales you have collected
echo   - deploys the API
echo.
echo It takes a few minutes. At the end it prints two values to
echo paste into your WEBSITE's Cloudflare settings.
echo.
echo It is free, and safe to run again if anything goes wrong.
echo.
pause
echo.

nflcarddb setup-api
set CODE=%errorlevel%

if not "%CODE%"=="0" (
  echo.
  echo ==========================================================
  echo    Setup did not finish
  echo ==========================================================
  echo.
  echo The message above says what stopped it. Nothing is broken -
  echo you can run this again once it is sorted.
  echo.
  echo If it could not find Node.js, install it from
  echo   https://nodejs.org/
  echo and run this again.
)
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
