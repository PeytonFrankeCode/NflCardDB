@echo off
REM Asks Cloudflare what your database actually contains. Uploads nothing.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_cloudflare.bat"

set DBID=a887dd0e-d852-4ebc-98f0-0e01bc82ad0b

echo.
echo ==========================================================
echo    What is in your Cloudflare database
echo ==========================================================
echo.



python -m nflcarddb d1-push --account-id "!CF_ACCOUNT_ID!" --database-id %DBID% --verify-only
if errorlevel 1 goto EMPTY

echo.
echo   sales        - every sold listing, best offers included
echo   priced_sales - the ones with a real sale price (best offers have none)
echo   days         - how many separate days you have collected
echo   cards        - the card catalogue: one row per card, which is
echo                  what your website browses and sorts
echo   clean_cards  - of those, the ones whose prices agree well
echo                  enough to trust (quality=clean)
echo   active_keys  - keys your website can use to read this
echo.
echo If these look right, the website has the data.
echo.
echo If  sales  is large but  cards  is 0, the sales landed and the
echo catalogue did not - the website will find nothing to browse.
echo Fix that with resend-all.bat.
echo.
echo If  active_keys  is 0, no website can read this at all.
echo Fix that with website-key.bat.
goto END

:EMPTY
echo.
echo Nothing came back. Either the upload has not run yet
echo (double-click d1-push.bat), or the token / Account ID
echo above was not right.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
