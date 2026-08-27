@echo off
REM Ask eBay what it calls things, instead of keeping our own lists.
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
echo    Asking eBay what it calls things
echo ==========================================================
echo.
echo Every eBay card listing has a form the seller fills in -
echo Player, Set, Parallel/Variety, Season. Those are the
echo checkboxes down the left side of a search.
echo.
echo We have been reading the seller's headline and ignoring
echo that form. So the names of sets and inserts have been
echo hand-written lists that go out of date every time a new
echo product ships.
echo.
echo This reads eBay's lists instead.
echo.
echo eBay only shows the top 8 or so of each list on one page.
echo So this narrows the search year by year to find that
echo year's sets, then narrows by each set to find that set's
echo inserts and parallels. That is how you get the real lists
echo rather than the eight biggest.
echo.
echo It takes about 15 minutes, and it stops itself if eBay
echo starts pushing back. Run it again another day and it
echo picks up where it left off - the list keeps growing.
echo.

nflcarddb facets --drill seasons,sets --budget 200 --save-html data\html
if errorlevel 1 goto NOTHING

echo.
echo ==========================================================
echo    Send the list above to Claude
echo ==========================================================
echo.
echo Nothing was changed and nothing was saved to your sales.
echo This only checks whether eBay's own lists can be read.
echo.
goto END

:NOTHING
echo.
echo ==========================================================
echo    Nothing came back
echo ==========================================================
echo.
echo That is a real answer, not a crash - it means eBay's page
echo does not look the way Claude expected.
echo.
echo The page it read was saved in the  data\html  folder.
echo Send the newest file in there to Claude and it can be
echo fixed against the real thing.
echo.
echo If it said "blocked" or "not signed in" instead, that is
echo the usual eBay check - run  login.bat  and try again.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
