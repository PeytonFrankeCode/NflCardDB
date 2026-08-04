@echo off
REM Uploads your sales to Cloudflare D1 over HTTPS. No Node.js needed.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

REM Your database, already created and bound to the website.
set DBID=a887dd0e-d852-4ebc-98f0-0e01bc82ad0b

echo.
echo ==========================================================
echo    Uploading your sales to Cloudflare
echo ==========================================================
echo.

if "%CLOUDFLARE_API_TOKEN%"=="" (
  echo You need a Cloudflare API token. Create one here:
  echo   https://dash.cloudflare.com/profile/api-tokens
  echo   Create Token  -^>  Custom token  -^>  Account ^| D1 ^| Edit
  echo.
  set /p CFTOKEN=Paste your API token:
  set "CLOUDFLARE_API_TOKEN=!CFTOKEN!"
  echo.
)

if "%CF_ACCOUNT_ID%"=="" (
  echo Account ID - the long value on the right of your Cloudflare
  echo overview page at https://dash.cloudflare.com/
  echo.
  set /p CFACCT=Paste your Account ID:
  set "CF_ACCOUNT_ID=!CFACCT!"
  echo.
)

echo Creating the tables (safe if they already exist)...
nflcarddb d1-push --account-id "!CF_ACCOUNT_ID!" --database-id %DBID% --schema api\schema.sql --schema-only
if errorlevel 1 goto FAILED
echo.

echo Uploading the sales...
nflcarddb d1-push --account-id "!CF_ACCOUNT_ID!" --database-id %DBID%
if errorlevel 1 goto FAILED

echo.
echo ==========================================================
echo    Uploaded
echo ==========================================================
echo.
echo Your website can read the data now.
echo.
echo TIP: to skip the prompts next time, set these once in
echo Windows (search "environment variables"):
echo   CLOUDFLARE_API_TOKEN
echo   CF_ACCOUNT_ID
goto END

:FAILED
echo.
echo ==========================================================
echo    Upload did not finish
echo ==========================================================
echo.
echo The message above says why. Common causes:
echo   - token missing the  Account ^| D1 ^| Edit  permission
echo   - wrong Account ID
echo   - no sales collected yet (run collect.bat first)
echo.
echo Nothing is half-written - every upload is an upsert, so
echo running this again is safe.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
