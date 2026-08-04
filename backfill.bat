@echo off
REM Collects the last 30 days in one go. Safe to stop and restart.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Catching up on past sales
echo ==========================================================
echo.
echo eBay only keeps sold listings for about 90 days. Anything
echo older than that is gone for good, so it is worth grabbing
echo the recent past now.
echo.
echo This takes roughly 10 MINUTES PER DAY collected, so 30
echo days is about 5 hours. You can leave it running, or close
echo the window and run it again later - days already collected
echo are skipped, so nothing is repeated or lost.
echo.
set /p DAYS=How many days back? (press Enter for 30):
if "%DAYS%"=="" set DAYS=30
echo.

nflcarddb -v backfill --days %DAYS%
set CODE=!errorlevel!
echo.

if "!CODE!"=="8" goto SIGNEDOUT
if "!CODE!"=="4" goto BLOCKED

echo Updating your dashboard...
nflcarddb publish
echo.
nflcarddb stats
echo.
echo ==========================================================
echo    Done
echo ==========================================================
echo.
echo To publish: open GitHub Desktop, type anything in the
echo Summary box, click Commit, then Push.
goto END

:SIGNEDOUT
echo ==========================================================
echo    Signed out partway through
echo ==========================================================
echo.
echo Everything collected so far IS saved.
echo.
echo Run  login.bat , then run this again - it picks up where
echo it stopped.
goto END

:BLOCKED
echo ==========================================================
echo    eBay asked for a human check
echo ==========================================================
echo.
echo Everything collected so far IS saved.
echo.
echo Wait an hour or two, then run this again. It skips the
echo days it already has.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
