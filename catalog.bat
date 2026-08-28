@echo off
REM Build the card vocabulary from eBay's API instead of scraping its HTML.
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"
REM Proof the helper returned. A silent window used to be ambiguous
REM between "the update check hung" and "the program printed nothing".
echo    [ready]

echo.
echo ==========================================================
echo    Asking eBay's API what it calls things
echo ==========================================================
echo.
echo Your API key covers ACTIVE listings, not sold ones - so it
echo cannot replace the collector. Sold prices still have to be
echo scraped.
echo.
echo What it can do is far better than scraping the sidebar:
echo it returns EVERY set, insert, parallel and player name,
echo with counts, as data. The web page only ever shows the top
echo eight of each, which is why the last three attempts kept
echo coming back with 32 sets.
echo.
echo The same cards are on sale now as sold last week, so the
echo names carry straight over.
echo.
echo This starts clean each time. One API call returns a whole
echo list, so there is nothing to build up - and keeping old
echo results only preserved mistakes from earlier attempts.
echo.

if not exist data\ebay-api.txt goto ASK
goto RUN

:ASK
echo ----------------------------------------------------------
echo    Your eBay keys (one time)
echo ----------------------------------------------------------
echo.
echo Open  https://developer.ebay.com/my/keys  and find the
echo PRODUCTION pair. Two long strings:
echo.
echo   App ID   (also called Client ID)
echo   Cert ID  (also called Client Secret)
echo.
set /p APPID=Paste the App ID:
set /p CERTID=Paste the Cert ID:
echo.
python -m nflcarddb catalog --app-id "%APPID%" --cert-id "%CERTID%" --apply
if errorlevel 1 goto BROKEN
goto DONE

:RUN
python -m nflcarddb catalog --apply
if errorlevel 1 goto BROKEN

:DONE
echo.
echo ==========================================================
echo    Send the list above to Claude
echo ==========================================================
echo.
echo eBay's lists are now the vocabulary, and every title has
echo been re-read with them. The hand-written lists are no
echo longer used.
echo.
echo Your keys are saved in data\ebay-api.txt so this only asks
echo once. That file is never uploaded to GitHub.
goto END

:BROKEN
echo.
echo ==========================================================
echo    That did not work
echo ==========================================================
echo.
echo The message above is eBay's own, and it is usually specific.
echo.
echo "refused the keys" almost always means the SANDBOX keys were
echo pasted instead of the PRODUCTION ones - they sit on separate
echo tabs of the same page and look identical.
echo.
echo To re-enter them, delete  data\ebay-api.txt  and run this
echo again.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
