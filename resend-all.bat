@echo off
REM Re-sends EVERY row to Cloudflare, ignoring what was uploaded before.
REM
REM The everyday upload (d1-push.bat) only sends what changed. That is right
REM almost always -- but after a regroup that renamed cards, or an upload that
REM stopped partway, Cloudflare can be holding the old grouping while this PC
REM believes it already sent the new one. This is the button that settles it.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"
call "%~dp0_cloudflare.bat"

set DBID=a887dd0e-d852-4ebc-98f0-0e01bc82ad0b

echo.
echo ==========================================================
echo    Send everything again
echo ==========================================================
echo.
echo This uploads your whole database, not just the new days.
echo Nothing is lost either way - every row is an upsert, so a
echo re-send overwrites with the same or better information.
echo.
echo It takes a while and it reads and writes a lot at
echo Cloudflare's end. Use it when the card names or the sorting
echo on your website look out of date, not as a routine.
echo.
pause

echo.
echo Creating the tables (safe if they already exist)...
python -m nflcarddb d1-push --account-id "!CF_ACCOUNT_ID!" --database-id %DBID% --schema api\schema.sql --schema-only
if errorlevel 1 goto FAILED
echo.

echo Sending everything...
python -m nflcarddb d1-push --account-id "!CF_ACCOUNT_ID!" --database-id %DBID% --full
if errorlevel 1 goto FAILED

echo.
echo ==========================================================
echo    Done - Cloudflare now matches this PC
echo ==========================================================
echo.
echo Check it with d1-check.bat. The line that matters for the
echo website's browsing and sorting is  cards  - that is the
echo card catalogue, one row per card. If it is 0, the sorting
echo has nothing to sort.
goto END

:FAILED
echo.
echo ==========================================================
echo    Did not finish
echo ==========================================================
echo.
echo The message above says why.
echo.
echo If it mentions a LIMIT or a QUOTA, Cloudflare's free plan
echo ran out for the day. It resets at midnight UTC (7pm or 8pm
echo Eastern). Nothing is half-written - run this again after.
echo.
echo A re-send does start over from the beginning: this button
echo deliberately ignores what was already uploaded. Rows that
echo did land get written over with the same values, so running
echo it twice costs time and nothing else.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
