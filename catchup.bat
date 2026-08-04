@echo off
REM Shows how much of eBay's 90-day window you have, and fills in the gaps.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Catching up on past sales
echo ==========================================================
echo.
nflcarddb coverage
echo.
echo eBay only keeps sold listings for about 90 days. Days that
echo age out are gone for good, so the sooner these are
echo collected the more of them still exist.
echo.
echo   1  Collect now, for a few hours
echo   2  Collect now, until it is finished (may run all night)
echo   3  Leave it to the daily schedule
echo   4  Nothing, just close
echo.
set /p PICK=Type a number and press Enter:

if "!PICK!"=="1" goto HOURS
if "!PICK!"=="2" goto ALL
if "!PICK!"=="3" goto SCHEDULED
goto END

:HOURS
echo.
set /p HRS=How many hours? (press Enter for 3):
if "!HRS!"=="" set HRS=3
set /a MINS=!HRS! * 60
echo.
echo Collecting for !HRS! hour(s). You can close this window at
echo any time - finished days are saved as they go.
echo.
nflcarddb -v backfill --days 90 --max-minutes !MINS!
goto FINISH

:ALL
echo.
echo Collecting everything still missing. This can take many
echo hours. Closing the window is safe - finished days are
echo saved as they go, and running this again resumes.
echo.
nflcarddb -v backfill --days 90
goto FINISH

:SCHEDULED
echo.
echo Fine. The daily run already spends up to 3 hours a night
echo on this, so it will work backwards on its own. Set it up
echo with  schedule.bat  if you have not yet.
goto END

:FINISH
set CODE=!errorlevel!
echo.
if "!CODE!"=="8" goto SIGNEDOUT
if "!CODE!"=="4" goto BLOCKED

echo Updating photos and your dashboard...
nflcarddb images --upgrade
nflcarddb publish
echo.
nflcarddb coverage
echo.
echo ==========================================================
echo    Stopped for now
echo ==========================================================
echo.
echo Everything collected is saved. Run this again any time to
echo carry on where it left off.
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
