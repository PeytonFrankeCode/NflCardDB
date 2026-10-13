@echo off
REM Drag a saved SOLD best-offer listing onto this file. It shows what the
REM best-offer lookup would read off that page. Nothing is written anywhere.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"

if "%~1"=="" goto NOARGS

set "ARGS="
:collect
if "%~1"=="" goto ready
set ARGS=!ARGS! --file "%~1"
shift
goto collect
:ready

echo.
echo ==========================================================
echo    What the lookup reads off this page
echo ==========================================================
echo.
python -m nflcarddb offers !ARGS!
set CODE=!errorlevel!
echo.

if "!CODE!"=="0" goto WORKED

echo ==========================================================
echo    It did NOT find the accepted price
echo ==========================================================
echo.
echo Look at the list of dollar amounts above. If the price the
echo buyer actually paid is in it, the page is laid out in a way
echo the lookup does not recognise yet - send this saved file to
echo Claude and it can be taught.
echo.
echo If the paid price is NOT anywhere in that list, eBay did not
echo show it on this page. Try a different sold best offer.
echo.
echo Lookups stay switched off until this check passes, so
echo nothing wrong has been or will be written.
goto END

:WORKED
echo ==========================================================
echo    It works - the lookup can read this page
echo ==========================================================
echo.
echo Check the ACCEPTED OFFER above matches what eBay shows. If it
echo does, switch the nightly lookups on:
echo.
echo   1. Open config\queries.yml in Notepad
echo   2. Find   offer_lookup_minutes: 0
echo   3. Change the 0 to how many minutes a night to spend, e.g. 45
echo.
echo Each lookup is one page, about 4 seconds, so 45 minutes is
echo roughly 650 sales a night, most valuable first. Or run
echo offers.bat to do a batch right now.
goto END

:NOARGS
echo.
echo How to use this:
echo.
echo   1. On eBay, open a SOLD listing that says "Best offer
echo      accepted" and shows the price that was paid.
echo   2. Press Ctrl+S. "Webpage, Complete" or "Single File"
echo      both work. Save it anywhere.
echo   3. Drag the saved file onto offer-check.bat.
echo.
echo Nothing is uploaded or changed. It only reports what it
echo would read off the page.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
