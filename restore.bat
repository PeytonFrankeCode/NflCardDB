@echo off
REM Rebuilds your sales database by downloading it back from Cloudflare.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_cloudflare.bat"

REM A recovery run is the worst possible time to be running old code, and this
REM was the one script that did not check.
call "%~dp0_update.bat"
echo    [ready]

set DBID=a887dd0e-d852-4ebc-98f0-0e01bc82ad0b

echo.
echo ==========================================================
echo    Getting your sales back from Cloudflare
echo ==========================================================
echo.
echo Your sales were uploaded to Cloudflare, which is not
echo affected by anything that happened to this PC. This
echo downloads them back into a local database.
echo.



python -m nflcarddb d1-pull --account-id "!CF_ACCOUNT_ID!" --database-id %DBID%
if errorlevel 1 goto FAILED

echo.
echo Refreshing your dashboard...
python -m nflcarddb publish
echo.
python -m nflcarddb coverage

echo.
echo ==========================================================
echo    Restored
echo ==========================================================
echo.
echo What came back: every sale you had uploaded.
echo What did not: run history, and anything collected but
echo never pushed to Cloudflare. Neither affects your prices.
echo.
echo Next: login.bat to sign in again, then collect.bat.
goto END

:FAILED
echo.
echo ==========================================================
echo    Could not restore
echo ==========================================================
echo.
echo The message above says why - usually the token is missing
echo the  Account ^| D1 ^| Edit  permission, or the Account ID
echo was pasted with a space.
echo.
echo Nothing was damaged. Your data is still on Cloudflare.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
