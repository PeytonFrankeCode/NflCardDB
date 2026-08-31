@echo off
REM Load the checklists from thecardhuddle.com: what cards actually exist.
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

REM An out-of-date checkout reports old numbers that look perfectly normal.
call "%~dp0_update.bat"
REM Proof the helper returned. A silent window used to be ambiguous
REM between "the update check hung" and "the program printed nothing".
echo    [ready]

echo.
echo ==========================================================
echo    Reading your checklists
echo ==========================================================
echo.
echo Everything else in this program works backwards: it reads
echo what a seller typed and tries to work out which card they
echo meant. That fails whenever the seller left something out,
echo and they leave a lot out - half of all titles have no card
echo number in them at all.
echo.
echo A checklist works forwards. It is the list of cards that
echo were actually printed, so a card can be looked up instead
echo of guessed at. It is the only thing that can say:
echo.
echo   - which insert a number belongs to
echo     ("#TD-34" is a Touchdown card, and the title never says)
echo   - what a set's colours are really called
echo     (Prizmania and Kaiju are not colours, so guessing missed
echo      them, and their cards merged into the base card)
echo   - whether a card we think we read actually exists
echo.
echo This runs on YOUR pc because the site is reachable from
echo here. It reads 361 products and takes a couple of minutes.
echo.

REM Look before importing. The field names on the site were never
REM visible while this was written, so the first run reports what
REM came back instead of quietly importing nothing.
echo Checking what the site returns first...
echo.
python -m nflcarddb checklists --look
if errorlevel 1 goto MISMATCH

echo.
echo ==========================================================
echo    Importing
echo ==========================================================
echo.
python -m nflcarddb checklists
if errorlevel 1 goto BROKEN

echo.
echo ==========================================================
echo    Done
echo ==========================================================
echo.
echo Nothing has changed about your sales yet - this only
echo loaded the list of cards that exist. Run  cards.bat  next
echo and the grouping will start using it.
echo.
goto END

:MISMATCH
echo.
echo ==========================================================
echo    The site does not look the way Claude guessed
echo ==========================================================
echo.
echo That is a real answer, not a crash. Claude could not reach
echo thecardhuddle.com from where it runs, so the field names
echo above are a guess that missed.
echo.
echo Copy everything printed above and send it to Claude. It
echo names the real fields, and the mapping is a small fix.
echo.
echo Nothing was imported and your sales are untouched.
goto END

:BROKEN
echo.
echo ==========================================================
echo    That did not finish
echo ==========================================================
echo.
echo The message above says why. Your sales are untouched -
echo this only adds a separate list of which cards exist.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
