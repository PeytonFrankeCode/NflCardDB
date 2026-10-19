@echo off
REM How big is each search? One request per query; nothing is stored.
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"

echo.
python -m nflcarddb survey
if errorlevel 1 goto FAILED

echo.
echo ==========================================================
echo    Reading it
echo ==========================================================
echo.
echo Each line is how many sold listings eBay says one search
echo covers. A + means eBay stopped counting at its ceiling.
echo.
echo   football_slabs  must be FAR SMALLER than football_singles.
echo                   It asks for graded cards only. If the two
echo                   come back about the same size, eBay ignored
echo                   the graded filter: open config\queries.yml,
echo                   put a # in front of the football_slabs lines
echo                   and remove the # from the football_graded
echo                   lines below them.
echo.
echo   Any search showing 0 is broken - eBay returns nothing
echo   rather than an error for a filter it does not accept.
goto END

:FAILED
echo.
echo The survey did not finish. If eBay asked for a human check,
echo wait an hour or two and run this again.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
