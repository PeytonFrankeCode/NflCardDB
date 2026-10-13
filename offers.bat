@echo off
REM Look up accepted Best Offer prices now, most valuable sales first.
REM Run offer-check.bat on one saved page before trusting this.
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"

echo.
echo ==========================================================
echo    Reading accepted Best Offer prices
echo ==========================================================
echo.
echo eBay's search results show a best-offer sale at the price the
echo seller ASKED. The buyer paid less. The real price is only on
echo the listing's own page, so this opens those pages one at a
echo time - the expensive sales first, where the ask is furthest
echo off in dollars - and corrects the price it finds there.
echo.
echo It runs for up to 45 minutes. Stop it any time with Ctrl+C;
echo everything it confirmed so far is kept.
echo.
pause

python -m nflcarddb offers --minutes 45
set CODE=%errorlevel%
echo.

if "%CODE%"=="10" goto MARKUP
if "%CODE%"=="8" goto SIGNEDOUT
if "%CODE%"=="4" goto BLOCKED

echo ==========================================================
echo    Done
echo ==========================================================
echo.
echo The corrected prices are in your database. They go up to
echo your website with the next upload - d1-push.bat now, or the
echo nightly run will send them.
goto END

:MARKUP
echo ==========================================================
echo    Stopped: the pages do not look the way it expects
echo ==========================================================
echo.
echo It read the first pages and found no accepted price on any
echo of them, so it stopped instead of spending the time. Nothing
echo wrong was written. Run offer-check.bat on one saved sold
echo best-offer listing to see what it is missing.
goto END

:SIGNEDOUT
echo Stopped: the eBay session expired. Run login.bat once.
goto END

:BLOCKED
echo Stopped: eBay asked for a human check. Try again later.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
