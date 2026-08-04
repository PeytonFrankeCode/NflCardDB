@echo off
REM Drag saved eBay pages (or a folder of them) onto this file.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

if "%~1"=="" goto NOARGS

REM Dropped items arrive as separate arguments; quote each one.
set "ARGS="
:collect
if "%~1"=="" goto ready
set ARGS=!ARGS! "%~1"
shift
goto collect
:ready

echo.
echo ==========================================================
echo    Reading the pages you saved
echo ==========================================================
echo.

nflcarddb import !ARGS!
set CODE=!errorlevel!
echo.

if not "!CODE!"=="0" goto FAILED

echo Updating your dashboard...
nflcarddb publish
echo.
nflcarddb stats
echo.
echo ==========================================================
echo    Done
echo ==========================================================
echo.
echo To put this on your website:
echo   1. Open GitHub Desktop.
echo   2. Type anything in the "Summary" box.
echo   3. Click "Commit to ...", then "Push origin".
goto END

:FAILED
echo ==========================================================
echo    Could not read those pages
echo ==========================================================
echo.
echo Check that you saved a SOLD listings page - the search
echo results with "Sold" next to each price - and that you
echo chose "Webpage, Complete" when saving.
goto END

:NOARGS
echo.
echo Nothing was dropped onto this file.
echo.
echo HOW TO USE:
echo   1. Open  tools\grabber.html  and drag its blue button
echo      onto your bookmarks bar. You only do this once.
echo   2. Search eBay as normal, tick "Sold items", and set the
echo      page size to 240.
echo   3. Click the bookmark. A small .json file downloads.
echo   4. Drag that file onto this import.bat file.
echo.
echo You can drag several files, or a whole folder, at once.
echo Saved web pages (Ctrl+S) work too, if you prefer.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
