@echo off
REM Read the card out of the listing photo and compare it with the title.
REM Measurement only - nothing is saved to your database.
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Reading cards from their photos
echo ==========================================================
echo.
echo A graded card's slab has a label printed on it - year, set,
echo player, card number - put there by the grader rather than
echo the seller. That label is readable, and it is a second
echo opinion on what the title claims.
echo.
echo This is NOT switched on yet. It reads a sample of photos,
echo compares them with the titles, and reports how often they
echo agree. Your database is not touched.
echo.

python -c "import rapidocr_onnxruntime" 2>nul
if errorlevel 1 goto INSTALL
goto RUN

:INSTALL
echo ----------------------------------------------------------
echo    One-time download needed (about 200 MB)
echo ----------------------------------------------------------
echo.
echo Reading text out of a picture needs a text-recognition
echo engine. It installs from pip - nothing to click through,
echo no separate program - but it is a few hundred megabytes.
echo.
set /p OK=Download it now? (y/n):
if /i not "%OK%"=="y" goto SKIPPED
echo.
pip install rapidocr-onnxruntime pillow
if errorlevel 1 goto BROKEN
echo.

:RUN
echo Reading 50 photos. About a second each.
echo.
nflcarddb vision --limit 50
if errorlevel 1 goto BROKEN

echo.
echo ----------------------------------------------------------
echo.
echo That was sales the title already identified, so it measures
echo whether the photo AGREES.
echo.
set /p MORE=Now try sales the title could NOT identify? (y/n):
if /i not "%MORE%"=="y" goto END
echo.
nflcarddb vision --limit 50 --unclear
goto END

:SKIPPED
echo.
echo Nothing installed, nothing changed. Run this again whenever.
goto END

:BROKEN
echo.
echo ==========================================================
echo    That did not finish
echo ==========================================================
echo.
echo The message above says why. Nothing was saved either way -
echo this command only ever reads.
echo.
echo If it is a long Python traceback, send it to Claude.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
