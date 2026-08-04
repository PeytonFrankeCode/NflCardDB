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
nflcarddb images --upgrade
nflcarddb publish
echo.

REM Upload without asking when the credentials are already stored. Prompting
REM here would be a second manual step for something that can just happen.
if "%CLOUDFLARE_API_TOKEN%"=="" goto NOCLOUD
if "%CF_ACCOUNT_ID%"=="" goto NOCLOUD

set DBID=a887dd0e-d852-4ebc-98f0-0e01bc82ad0b
echo Uploading to your website...
nflcarddb d1-push --account-id "%CF_ACCOUNT_ID%" --database-id %DBID% --schema api\schema.sql --schema-only
nflcarddb d1-push --account-id "%CF_ACCOUNT_ID%" --database-id %DBID%
if errorlevel 1 (
  echo.
  echo Upload did not finish - your data is still safe on this PC.
  echo Run  d1-push.bat  to try again.
)
echo.
goto SUMMARY

:NOCLOUD
echo Skipping the website upload - this PC is not connected to
echo Cloudflare yet. Double-click  connect-cloudflare.bat  once
echo and it will happen automatically from then on.
echo.

:SUMMARY
nflcarddb stats
echo.
echo ==========================================================
echo    Done
echo ==========================================================
echo.
echo For the public dashboard: open GitHub Desktop, type
echo anything in the Summary box, click Commit, then Push.
echo.
echo Tired of doing this daily? Double-click  schedule.bat.
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
