@echo off
REM How well are listing titles being read? Two answers: one free, one honest.
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

REM An out-of-date checkout reports old numbers that look perfectly normal.
call "%~dp0_update.bat"

echo.
echo ==========================================================
echo    How accurate is the card matching?
echo ==========================================================
echo.
echo Making sure every sale has been matched to a card first.
echo Sales collected before this feature existed need one pass;
echo on a large database it takes a minute or two.
echo.

nflcarddb parse --all
if errorlevel 1 goto BROKEN
echo.

nflcarddb audit
if errorlevel 1 goto END

echo.
echo ==========================================================
echo.
echo   1  Draw 100 sales to check by hand (gives a real %%)
echo   2  Score a sample you have already filled in
echo   3  Nothing, just close
echo.
set /p PICK=Type a number and press Enter:

if "%PICK%"=="1" goto DRAW
if "%PICK%"=="2" goto SCORE
goto END

:DRAW
echo.
nflcarddb review --sample 100
echo.
echo Opening it for you...
start "" review-sample.csv
goto END

:SCORE
echo.
nflcarddb review --score review-sample.csv
goto END

:BROKEN
echo.
echo ==========================================================
echo    Something went wrong before we got started
echo ==========================================================
echo.
echo The message above says what. Nothing was changed.
echo.
echo If it is a long Python traceback, that is a bug - send it
echo to Claude rather than trying to read it.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
