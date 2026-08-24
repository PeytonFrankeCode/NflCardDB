@echo off
REM How well are listing titles being read? Two answers: one free, one honest.
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    How accurate is the card matching?
echo ==========================================================
echo.

nflcarddb audit

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

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
