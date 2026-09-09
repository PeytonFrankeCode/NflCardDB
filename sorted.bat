@echo off
REM Shows the sorted cards, read live out of Cloudflare - the same question
REM your website asks it. Uploads nothing and changes nothing.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"
call "%~dp0_cloudflare.bat"

set DBID=a887dd0e-d852-4ebc-98f0-0e01bc82ad0b

echo.
echo ==========================================================
echo    Your cards, sorted, live from Cloudflare
echo ==========================================================

echo.
echo MOST TRADED - the cards that change hands most
echo.
python -m nflcarddb d1-cards --account-id "!CF_ACCOUNT_ID!" --database-id %DBID% --sort traded --limit 15
if errorlevel 1 goto FAILED

echo.
echo ==========================================================
echo BIGGEST RISERS - price moving up, cards with a real market
echo.
python -m nflcarddb d1-cards --account-id "!CF_ACCOUNT_ID!" --database-id %DBID% --sort rising --min-sales 8 --limit 15

echo.
echo ==========================================================
echo HIGHEST VALUE
echo.
python -m nflcarddb d1-cards --account-id "!CF_ACCOUNT_ID!" --database-id %DBID% --sort value --limit 15

echo.
echo ==========================================================
echo    That is your website's data
echo ==========================================================
echo.
echo Nothing was computed just now. Those orderings are stored
echo in the database as columns, so your site sorts by reading
echo them - it does no work of its own.
echo.
echo The full list of sorts and filters is in  api\SQL.md.
goto END

:FAILED
echo.
echo Could not read the cards. If it says the catalogue is empty,
echo run d1-check.bat and look at the  cards  line.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
