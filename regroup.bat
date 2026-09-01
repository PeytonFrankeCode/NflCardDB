@echo off
REM Regroup every sale you have, then send it all to the website database.
REM
REM Deliberately one script rather than four. The order matters and getting it
REM wrong is silent: regrouping before the checklist is loaded just redoes the
REM old grouping, and uploading before regrouping sends the old keys.
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"
echo    [ready]

echo.
echo ==========================================================
echo    Regrouping everything
echo ==========================================================
echo.
echo Four steps, in this order, because each one needs the one
echo before it:
echo.
echo   1. load the checklist    (what cards exist)
echo   2. re-read every title   (using it)
echo   3. refresh the dashboard files
echo   4. upload to Cloudflare  (your other site reads this)
echo.
echo Around ten minutes in total. Nothing is deleted at any
echo point - every step rewrites, so running it twice is safe.
echo.
echo The checklist file is found automatically. Keep it in
echo   data\checklists\   (or just leave it in Downloads)
echo and this needs nothing from you but a double-click.
echo.

REM ---- 1. checklist --------------------------------------------------------
REM No path to type and nothing to drag. The checklist export is looked for in
REM data\checklists, this folder, and your Downloads - so saving the file once
REM is enough, and every future run finds it on its own.
echo [1/4] Loading the checklist ...
if "%~1"=="" goto FINDIT
echo       using the file you dropped on this script
python -m nflcarddb checklists --csv "%~1"
if errorlevel 1 goto NOCHECKLIST
goto REPARSE

:FINDIT
python -m nflcarddb checklists
if errorlevel 1 goto NOCHECKLIST

REM ---- 2. reparse ----------------------------------------------------------
:REPARSE
echo.
echo [2/4] Re-reading every title against it ...
echo       This is the step that changes how sales group together.
python -m nflcarddb parse --all
if errorlevel 1 goto BROKEN

echo.
echo Your cards now:
python -m nflcarddb cards --limit 25

REM ---- 3. dashboard --------------------------------------------------------
echo.
echo [3/4] Refreshing the dashboard files ...
python -m nflcarddb publish >nul
if errorlevel 1 goto BROKEN

REM ---- 4. upload -----------------------------------------------------------
echo.
echo [4/4] Uploading to Cloudflare ...
call "%~dp0_cloudflare.bat"
if "%CF_ACCOUNT_ID%"=="" goto NOCLOUD

set DBID=a887dd0e-d852-4ebc-98f0-0e01bc82ad0b
python -m nflcarddb d1-push --account-id "%CF_ACCOUNT_ID%" --database-id %DBID% --schema api\schema.sql --schema-only
if errorlevel 1 goto UPLOADFAILED
python -m nflcarddb d1-push --account-id "%CF_ACCOUNT_ID%" --database-id %DBID%
if errorlevel 1 goto UPLOADFAILED

echo.
echo ==========================================================
echo    Done - your other site can read it now
echo ==========================================================
echo.
echo Every sale has been regrouped and the whole catalogue is
echo in Cloudflare, including which cards are trustworthy.
echo.
echo Your site can now ask for just the good ones:
echo    /v1/cards?quality=clean^&sort=rising
echo.
goto END

:NOCHECKLIST
echo.
echo ==========================================================
echo    The checklist did not load
echo ==========================================================
echo.
echo Stopped here on purpose. Regrouping without it would just
echo redo the grouping you already have, which looks like the
echo whole thing worked and changes nothing.
echo.
echo The message above says why. If it is about field names,
echo send it to Claude - that is a small fix.
goto END

:UPLOADFAILED
echo.
echo ==========================================================
echo    Regrouped here, but the upload did not finish
echo ==========================================================
echo.
echo Your sales ARE regrouped on this pc - only the upload
echo failed, and nothing is half-written. Common causes:
echo   - token missing the  Account ^| D1 ^| Edit  permission
echo   - wrong Account ID
echo.
echo Run  d1-push.bat  on its own to try the upload again.
goto END

:NOCLOUD
echo.
echo Regrouped here, but no Cloudflare credentials are stored,
echo so nothing was uploaded. Run  connect-cloudflare.bat  once
echo and then  d1-push.bat.
goto END

:BROKEN
echo.
echo ==========================================================
echo    That did not finish
echo ==========================================================
echo.
echo The message above says why. Your sales are untouched -
echo every step here rewrites rather than deletes.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
