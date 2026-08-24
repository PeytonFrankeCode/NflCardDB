@echo off
REM Learn player names from the titles already collected, then re-read
REM every title with them. This is what pulls scattered sales of one card
REM into a single price history.
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Learning player names from your own data
echo ==========================================================
echo.
echo Sellers write titles like "BOMB SQUAD JAYDEN DANIELS", and
echo without knowing which words are the player, the insert name
echo gets read as part of it. The same card then splits into
echo several, and its price history splits with it.
echo.
echo This works out which phrases are players by looking at how
echo widely they appear: a player turns up in Prizm, Mosaic,
echo Donruss, across years. An insert lives in one set of one
echo year, because that is what an insert is.
echo.
echo It then re-reads every title you have collected. On a large
echo database that takes a few minutes. Leave it running.
echo.

nflcarddb roster
if errorlevel 1 goto BROKEN

echo.
echo ==========================================================
echo    Done
echo ==========================================================
echo.
echo Re-run this whenever you have collected a lot more sales -
echo the list gets better as the data grows, and it costs
echo nothing to rebuild.
echo.
echo To see the full quality report, double-click accuracy.bat
echo.
goto END

:BROKEN
echo.
echo ==========================================================
echo    That did not finish
echo ==========================================================
echo.
echo The message above says why. Your sales are untouched.
echo.
echo "Not enough collected data" means exactly that - collect
echo a few more days and try again.
echo.
echo If it is a long Python traceback, that is a bug - send it
echo to Claude rather than trying to read it.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
pause
