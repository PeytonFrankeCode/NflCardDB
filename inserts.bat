@echo off
REM Learn the names of insert sets from your own titles, so cards that share a
REM number stop sharing a price history.
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
echo    Learning the names of insert sets
echo ==========================================================
echo.
echo An insert set restarts its numbering at 1. So Phoenix
echo "Contours #8", "Genies #8" and "Archetype #8" are three
echo different cards that all look like card number 8 - and
echo without the insert name they share one price history.
echo.
echo There are a dozen new insert names in every product every
echo year, so a hand-written list is always out of date. This
echo works them out from your own listings: an insert is the
echo opposite of a player. A player turns up across many sets
echo and years. An insert lives in one product, next to lots of
echo different players.
echo.

python -m nflcarddb inserts
if errorlevel 1 goto BROKEN

echo.
echo ==========================================================
echo    Now check the list
echo ==========================================================
echo.
echo This is a PROPOSAL, not a decision. Opening it for you.
echo.
echo Each line has the evidence beside it. Delete any line that
echo is not really the name of an insert set - a wrong entry
echo splits a card in two, which is worse than the problem it
echo is fixing.
echo.
start "" config\nfl_inserts.txt
echo.
set /p OK=Happy with the list? Turn it on now? (y/n):
if /i not "%OK%"=="y" goto LATER

echo.
python -m nflcarddb inserts --apply
if errorlevel 1 goto BROKEN
echo.
echo Done. Double-click accuracy.bat to see what changed.
goto END

:LATER
echo.
echo Nothing turned on. Edit the file whenever you like, then
echo run this again and answer y.
goto END

:BROKEN
echo.
echo ==========================================================
echo    That did not finish
echo ==========================================================
echo.
echo The message above says why. Your sales are untouched.
echo.
echo "A roster is needed first" means run names.bat before this
echo one - working out which phrases are inserts needs to know
echo which ones are players.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
