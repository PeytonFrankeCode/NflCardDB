@echo off
REM Writes the WHOLE sorted catalogue to a spreadsheet you can open in Excel.
REM The preview (sorted.bat) shows fifteen rows; this is all of them.
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"

echo.
echo ==========================================================
echo    Every card, sorted, in a spreadsheet
echo ==========================================================
echo.
echo This reads your own database - nothing is downloaded and
echo nothing is uploaded, so it costs nothing and is quick.
echo.

python -m nflcarddb card-list --sort traded --quality clean --out data\cards-good.csv
if errorlevel 1 goto FAILED

echo.
python -m nflcarddb card-list --sort traded --quality all --out data\cards-all.csv
if errorlevel 1 goto FAILED

echo.
echo ==========================================================
echo    Two files in your data folder
echo ==========================================================
echo.
echo   data\cards-good.csv  - the trustworthy ones (quality=clean)
echo   data\cards-all.csv   - every card, every pile
echo.
echo Open either in Excel. Sort and filter any column you like -
echo the columns are the same ones your website can sort on.
echo.
echo Worth knowing about two of them:
echo.
echo   trend_pct   - price change, measured inside ONE grade. A
echo                 raw copy and a PSA 10 are two markets, and a
echo                 card that moved from one to the other is not
echo                 a card whose price moved.
echo   trend_sales - how many sales that change is based on. A
echo                 huge move on 4 sales is barely evidence; the
echo                 same move on 40 is a market.
echo.
echo Sort by trend_pct and you will see big numbers at the top on
echo very few sales. That is normal and it is why trend_sales is
echo there - filter it to 10 or more first.
goto END

:FAILED
echo.
echo Could not build the list. If it says there is no catalogue,
echo collect a few more days first.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
