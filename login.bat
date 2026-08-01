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
echo If eBay shows a "verify you are human" puzzle here that you
echo cannot complete, close the window and press N below instead.
echo.
set "EXTRA="
set /p USECHROME=Use your everyday Chrome instead? (y/N):
if /i "%USECHROME%"=="y" (
  set "EXTRA=--chrome-profile"
  echo.
  echo IMPORTANT: close Google Chrome completely first - including
  echo anything still running in the system tray - or this cannot
  echo open your profile.
  echo.
  pause
)

nflcarddb login %EXTRA%
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
  echo.
  echo If eBay would not let you past its human-verification
  echo puzzle, use  import.bat  instead: browse eBay normally in
  echo your own Chrome, save the page with Ctrl+S, and drag it on.
)
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
