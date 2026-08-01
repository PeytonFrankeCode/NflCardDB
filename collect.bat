@echo off
REM Double-click this file whenever you want to gather sales.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Collecting yesterday's football card sales
echo ==========================================================
echo.
echo This usually takes 5-10 minutes. The collector goes slowly
echo on purpose, so eBay does not mistake it for an attack.
echo Leave this window open until it finishes.
echo.

nflcarddb -v scrape
set CODE=!errorlevel!
echo.

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
echo Your numbers are saved on this PC. To put them on your
echo website:
echo.
echo   1. Open GitHub Desktop.
echo   2. It will list the changed files on the left.
echo   3. Type anything in the "Summary" box, e.g.  new data
echo   4. Click "Commit to main".
echo   5. Click "Push origin" at the top.
echo.
echo Your dashboard updates a minute or two later.
goto END

:BLOCKED
echo ==========================================================
echo    eBay showed a robot check partway through
echo ==========================================================
echo.
echo Whatever was collected before that point HAS been saved -
echo nothing is lost, and running this again later picks up
echo where it stopped.
echo.
echo Wait an hour or two, then double-click collect.bat again.
goto END

:OFFLINE
echo ==========================================================
echo    Could not reach eBay
echo ==========================================================
echo.
echo Check that this PC is online, then try again.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first - that gets your PC
echo ready. You only ever need to do it once.
echo.

:END
echo.
pause
