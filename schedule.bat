@echo off
REM Sets up (or checks, or cancels) the daily automatic collection.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Daily automatic collection
echo ==========================================================
echo.
python -m nflcarddb schedule --status
echo.
echo   1  Turn it on (or change the time)
echo   2  Run it right now, to check it works
echo   3  Turn it off
echo   4  Nothing, just close
echo.
set /p PICK=Type a number and press Enter:

if "!PICK!"=="1" goto ON
if "!PICK!"=="2" goto NOW
if "!PICK!"=="3" goto OFF
goto END

:ON
echo.
echo What time each day? Use 24-hour, e.g. 07:00 or 21:30.
echo Pick a time this PC is usually on and you are signed in.
echo.
set /p WHEN=Time (just press Enter for 07:00):
if "!WHEN!"=="" set WHEN=07:00
echo.
python -m nflcarddb schedule --at "!WHEN!"
if errorlevel 1 goto FAILED
echo.

echo Each run collects yesterday, then works backwards through
echo the older days eBay still has. How long may it spend on
echo that older catch-up before stopping for the day?
echo.
set /p CMINS=Hours (press Enter for 3, or 0 for none):
if "!CMINS!"=="" set CMINS=3
set /a CATCHUP=!CMINS! * 60
setx NFLCARDDB_CATCHUP_MINUTES "!CATCHUP!" >nul
echo.
if "!CATCHUP!"=="0" (
  echo Catch-up off - it will only collect yesterday.
) else (
  python -m nflcarddb coverage
)
echo.
if "%CLOUDFLARE_API_TOKEN%"=="" (
  echo NOTE: it will collect and update the dashboard files, but
  echo it will NOT upload to your website until you double-click
  echo connect-cloudflare.bat once.
  echo.
)
echo Reminder: this collects and uploads by itself. Pushing to
echo GitHub for the public dashboard is still a manual Commit
echo and Push in GitHub Desktop.
goto END

:NOW
echo.
python -m nflcarddb schedule --run-now
echo.
echo It runs in the background - no window will appear. Look in
echo the  logs  folder in a few minutes to see how it went.
goto END

:OFF
echo.
python -m nflcarddb schedule --remove
echo.
echo Collection is back to manual - use collect.bat.
goto END

:FAILED
echo.
echo Could not set it up. The message above says why.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
