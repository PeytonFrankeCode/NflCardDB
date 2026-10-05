@echo off
REM Where a run's time goes, and what it would cost to cut it.
REM Reads your own database. Nothing is fetched, uploaded or changed.
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"

echo.
python -m nflcarddb speed --runs 14
if errorlevel 1 goto FAILED

echo.
echo ==========================================================
echo    The short version
echo ==========================================================
echo.
echo A run's length is PAGES times SECONDS PER PAGE. There is
echo nothing else in it, so there are only three ways to change
echo it - and only two are worth doing.
echo.
echo   FEWER SEARCHES    The big one. Every search walks back
echo                     through the calendar on its own, so six
echo                     searches page past the same recent days
echo                     six times. Drop the ones the report
echo                     above shows below 1.0 value.
echo.
echo   A SHORTER DELAY   Second biggest, and the only change
echo                     here that can bite. Try 1.5 for ONE
echo                     night, then check bot_checks is still
echo                     zero. One bot check costs more than a
echo                     night of the saving.
echo.
echo   FEWER PRICE BANDS Not worth it. Bands split the price
echo                     range, so seven of them read the same
echo                     listings one would. Cutting them saves
echo                     almost nothing and gives up the
echo                     protection against eBay's result cap.
echo.
echo Catching up on OLD days is slow for a reason no setting can
echo fix: eBay cannot be asked for one date, so the walk pages
echo back through everything sold since. Once you are caught up
echo and only collecting yesterday, runs get much shorter on
echo their own.
goto END

:FAILED
echo.
echo Could not read the database. If it says there are no
echo finished runs, collect a day first with collect.bat.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
