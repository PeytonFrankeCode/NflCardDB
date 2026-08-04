@echo off
REM Sign in once. After this, collecting runs on its own.
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Sign in once - then collecting is automatic
echo ==========================================================
echo.
echo eBay only shows sold prices to signed-in accounts, so the
echo collector needs its own signed-in session.
echo.
echo A Chrome window will open. Sign in there exactly as you
echo normally would. If eBay asks you to prove you are human,
echo do the puzzle yourself - that is the whole point of this
echo step happening in a real window with you sitting here.
echo.
echo TICK "Stay signed in" if eBay offers it. That keeps the
echo session alive for weeks instead of hours.
echo.
echo Your password goes straight to eBay. This project never
echo sees it. Only the signed-in session is kept, on this PC,
echo in the  data\browser-profile  folder.
echo.
pause
echo.

nflcarddb login
set CODE=%errorlevel%
echo.

if "%CODE%"=="0" (
  echo ==========================================================
  echo    Signed in - you are done here
  echo ==========================================================
  echo.
  echo From now on just run  collect.bat  and it works on its
  echo own. No Chrome to close, no windows to watch.
  echo.
  echo Come back and run this again only if collecting starts
  echo saying "signed out", which happens when the session
  echo eventually expires.
) else (
  echo ==========================================================
  echo    Could not confirm the sign-in
  echo ==========================================================
  echo.
  echo Run  collect.bat  anyway - the check only reads the page
  echo and can be wrong. The session is saved either way.
  echo.
  echo If collecting says "signed out", run this again and make
  echo sure you get all the way to the eBay homepage with your
  echo name showing in the corner before closing the window.
)
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
