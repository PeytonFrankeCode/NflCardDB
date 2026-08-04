@echo off
REM Pushes the newest sales up to the hosted API.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

where npx >nul 2>&1
if errorlevel 1 goto NONODE

echo.
echo ==========================================================
echo    Updating the hosted API
echo ==========================================================
echo.

echo Building the upload file...
nflcarddb export-api --out api\import.sql
if errorlevel 1 goto EXPORTFAIL
echo.

echo Uploading to Cloudflare...
cd api
npx wrangler d1 execute nflcarddb --remote --file=import.sql
set CODE=!errorlevel!
cd ..
echo.

if not "!CODE!"=="0" goto UPLOADFAIL
echo ==========================================================
echo    API updated
echo ==========================================================
echo.
echo Your website now sees the newest sales.
goto END

:EXPORTFAIL
echo Could not build the upload file. Collect some sales first.
goto END

:UPLOADFAIL
echo ==========================================================
echo    Upload failed
echo ==========================================================
echo.
echo If this says you are not logged in, run once:
echo    cd api  ^&^&  npx wrangler login
echo.
echo If it cannot find the database, you have not created it yet.
echo See API.md - it is a one-time setup.
goto END

:NONODE
echo.
echo Node.js is needed to talk to Cloudflare.
echo Install it from  https://nodejs.org/  then run this again.
echo.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
