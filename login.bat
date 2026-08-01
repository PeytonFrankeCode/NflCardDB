@echo off
REM Double-click this to sign in to eBay once, so the collector can see
REM sold listings. You only need to do this again if it stops working.
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Signing in to eBay
echo ==========================================================
echo.
echo eBay only shows sold prices to signed-in accounts, so the
echo collector needs to be signed in as you.
echo.
echo A browser window is about to open. Sign in there as normal,
echo then come back to THIS window and press Enter.
echo.
echo Your password goes straight to eBay. This project never
echo sees it - only the signed-in session is kept, on this PC.
echo.
pause

nflcarddb login
set CODE=%errorlevel%
echo.

if "%CODE%"=="0" (
  echo ==========================================================
  echo    Signed in
  echo ==========================================================
  echo.
  echo Next: double-click  doctor.bat  to confirm it can now
  echo read sold listings, then  collect.bat  to gather data.
) else (
  echo ==========================================================
  echo    Could not confirm sign-in
  echo ==========================================================
  echo.
  echo Run  doctor.bat  anyway - the check above is only reading
  echo the page and can be wrong. If doctor still says REFUSED,
  echo send its report to Claude.
)
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
