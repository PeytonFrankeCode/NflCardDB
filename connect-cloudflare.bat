@echo off
REM Stores your Cloudflare credentials once, so uploads stop asking for them.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

set DBID=a887dd0e-d852-4ebc-98f0-0e01bc82ad0b

echo.
echo ==========================================================
echo    Connect this PC to Cloudflare
echo ==========================================================
echo.
echo Do this once. After it, uploads run without asking, which
echo is what lets the daily schedule work on its own.
echo.

set "TOK=%CLOUDFLARE_API_TOKEN%"
if "!TOK!"=="" (
  echo API token - create one at:
  echo   https://dash.cloudflare.com/profile/api-tokens
  echo   Create Token  -^>  Custom token  -^>  Account ^| D1 ^| Edit
  echo.
  set /p TOK=Paste your API token:
  echo.
)

set "ACCT=%CF_ACCOUNT_ID%"
if "!ACCT!"=="" (
  echo Account ID - the long value on the right of your
  echo Cloudflare overview page at https://dash.cloudflare.com/
  echo.
  set /p ACCT=Paste your Account ID:
  echo.
)

echo Checking they work...
set "CLOUDFLARE_API_TOKEN=!TOK!"
nflcarddb d1-push --account-id "!ACCT!" --database-id %DBID% --verify-only
if errorlevel 1 goto BADCREDS

echo.
echo Saving them for next time...
if not exist data mkdir data
> data\cloudflare.txt echo token=!TOK!
>> data\cloudflare.txt echo account=!ACCT!
setx CLOUDFLARE_API_TOKEN "!TOK!" >nul
setx CF_ACCOUNT_ID "!ACCT!" >nul

echo.
echo ==========================================================
echo    Connected
echo ==========================================================
echo.
echo Uploads will not ask for these again.
echo.
echo Note: they are stored in data\cloudflare.txt and in your
echo Windows user settings, in plain text, like most tools do.
echo git ignores that file, so it cannot be committed. The token can only touch
echo Cloudflare databases - it cannot read your email, your
echo domains, or anything else. To revoke it, delete it in the
echo Cloudflare page above and run this again.
echo.
echo Next: schedule.bat, to have this happen daily by itself.
goto END

:BADCREDS
echo.
echo ==========================================================
echo    Those did not work
echo ==========================================================
echo.
echo Nothing was saved. The message above says which part
echo failed - usually the token is missing the
echo   Account ^| D1 ^| Edit
echo permission, or the Account ID was pasted with a space.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
