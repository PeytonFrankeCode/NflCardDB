@echo off
REM What is stopping cards from being usable, and what would actually fix it.
REM Reads your own database. Nothing is uploaded and nothing is changed.
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"

echo.
python -m nflcarddb unsorted --limit 25
if errorlevel 1 goto FAILED

echo.
echo ==========================================================
echo    What this is for
echo ==========================================================
echo.
echo Two different things get called "sorting":
echo.
echo   SORTING  - putting cards in order by price, sales, or
echo              trend. This is exact and it already works.
echo.
echo   GROUPING - deciding WHICH SALES ARE THE SAME CARD. This
echo              is the hard one, and it is what the numbers
echo              above are about.
echo.
echo A card number is what settles grouping. Without it, every
echo card that player had in that set piles into one row.
echo.
echo The two numbers under "WHAT WOULD FIX THOSE" decide what is
echo worth building next. If most of them are the machine kind,
echo reading photos pays for itself. If most are the person
echo kind, a review screen is the better use of the time.
goto END

:FAILED
echo.
echo Could not read the database. If it says no cards are
echo grouped yet, collect a few more days first.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
