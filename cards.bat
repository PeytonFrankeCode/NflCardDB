@echo off
REM Group every sale of the same card together, then refresh the website.
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
echo    Grouping your sales into cards
echo ==========================================================
echo.
echo Twenty people sell the same card. That is twenty rows in
echo your database, and on its own it tells you nothing - you
echo see twenty prices, not one card going up or down.
echo.
echo This reads every title again with the newest word lists,
echo works out which rows are the SAME physical card, and gives
echo each card one name and one price history.
echo.
echo Graded and raw copies stay together as one card. They are
echo the same cardboard sold in two different markets, so the
echo website lets you switch between them rather than pretending
echo they are separate cards.
echo.
echo Re-reading every title takes a minute or two.
echo.

nflcarddb parse --all
if errorlevel 1 goto BROKEN

echo.
echo Refreshing the website files...
nflcarddb publish >nul
if errorlevel 1 goto BROKEN

echo.
echo ==========================================================
echo    Your cards, busiest first
echo ==========================================================
echo.

nflcarddb cards --limit 40
if errorlevel 1 goto BROKEN

echo.
echo ==========================================================
echo    What to do next
echo ==========================================================
echo.
echo TREND is the second half of a card's sales against the
echo first half - not the newest sale against the oldest, so one
echo strange sale cannot invent a trend. Cards with fewer than
echo four sales show no trend at all, on purpose.
echo.
echo Cards that sold only once are left out entirely. One price
echo is not a history.
echo.
echo The website now has this too, with a chart per card. Push
echo it up with  d1-push.bat  to see it online.
echo.
goto END

:BROKEN
echo.
echo ==========================================================
echo    That did not finish
echo ==========================================================
echo.
echo The message above says why. Your sales are untouched - this
echo only re-reads titles you already collected.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
