@echo off
REM Collects yesterday's sales on its own, using the session from login.bat.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Collecting yesterday's football card sales
echo ==========================================================
echo.
echo This runs on its own. It takes 5-10 minutes and goes
echo slowly on purpose so eBay does not mistake it for an
echo attack. You can leave it and come back.
echo.

nflcarddb -v scrape --save-html data\html
set CODE=!errorlevel!
echo.

if "!CODE!"=="8" goto SIGNEDOUT
if "!CODE!"=="4" goto BLOCKED
if "!CODE!"=="5" goto OFFLINE

echo Updating your dashboard...
nflcarddb publish
echo.
nflcarddb stats
echo.
echo ==========================================================
echo    Done
echo ==========================================================
echo.
echo To put this on your website: open GitHub Desktop, type
echo anything in the Summary box, click Commit, then Push.
goto END

:SIGNEDOUT
echo ==========================================================
echo    Not signed in
echo ==========================================================
echo.
echo The saved session has expired, or has not been set up yet.
echo.
echo Double-click  login.bat , sign in once, and then run this
echo again. That is the only manual step there is.
goto END

:BLOCKED
echo ==========================================================
echo    eBay asked for a human check partway through
echo ==========================================================
echo.
echo Everything collected before that point HAS been saved.
echo Running this again later picks up where it stopped.
echo.
echo Wait an hour or two and try again.
goto END

:OFFLINE
echo ==========================================================
echo    Could not reach eBay
echo ==========================================================
echo.
echo Check this PC is online, then try again.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
