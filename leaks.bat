@echo off
REM Where the collector already knows it missed sales.
REM Reads your own database. Nothing is fetched, uploaded or changed.
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"

echo.
python -m nflcarddb leaks --runs 14
if errorlevel 1 goto FAILED

echo.
echo ==========================================================
echo    What to do about it
echo ==========================================================
echo.
echo There are only three reasons a sale does not reach you, and
echo the report above says which is costing you the most.
echo.
echo   A WALL      eBay will not page a single search past about
echo               10,000 results. No amount of time gets inside
echo               one - the search has to ask for less at once.
echo               Fix: narrower price bands (the report writes
echo               them out for you to paste in).
echo.
echo   NO TIME     The run stopped before it reached the day it
echo               was collecting, or ran out of pages with
echo               searches still queued. Fix: catchup.bat - it
echo               re-checks the partial days first, then keeps
echo               collecting for as long as you let it. Raise
echo               page_budget ONLY if the report says UNREACHED.
echo.
echo   NOT ASKED   No search covered it at all. This one never
echo               appears in the report, because the collector
echo               cannot miss what it was never told to look
echo               for. Fix: add a query.
echo.
echo The last one is usually the biggest. You run three searches;
echo eBay has far more football than three searches reach.
goto END

:FAILED
echo.
echo Could not read the database. If it says there are no runs
echo yet, collect a day first with collect.bat.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
