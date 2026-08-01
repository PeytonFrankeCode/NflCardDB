@echo off
REM Double-click this file once, to get your computer ready.
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ==========================================================
echo    NflCardDB - one-time setup
echo ==========================================================
echo.

REM ---- 1. Is Python installed and on PATH? -----------------------------
where python >nul 2>&1
if errorlevel 1 goto NOPYTHON

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Found Python !PYVER!
echo.

REM ---- 2. Private workspace so nothing else on your PC is touched ------
echo Building a private workspace. This takes a minute or two...
if exist venv goto HAVEVENV
python -m venv venv
if errorlevel 1 goto VENVFAIL
:HAVEVENV

call venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul 2>&1
echo Installing the collector...
pip install -e . >install-log.txt 2>&1
if errorlevel 1 goto INSTALLFAIL
echo Installed.
echo.

REM ---- 3. One request, to see whether eBay will talk to us -------------
echo ==========================================================
echo    Checking whether eBay will talk to your computer
echo ==========================================================
echo.
nflcarddb probe --query football_singles --save-html data\html
set CODE=!errorlevel!
echo.
echo ==========================================================
if "!CODE!"=="0" goto OK
if "!CODE!"=="4" goto BLOCKED
if "!CODE!"=="5" goto OFFLINE
if "!CODE!"=="1" goto NOLISTINGS
goto UNKNOWN

:OK
echo    IT WORKS.
echo ==========================================================
echo.
echo eBay returned real listings and the collector read them.
echo.
echo NEXT: double-click  collect.bat  to gather a day of sales.
goto END

:BLOCKED
echo    eBay showed a robot check
echo ==========================================================
echo.
echo eBay served a "verify you are human" page instead of results.
echo This happens - it is not something you did wrong.
echo.
echo Wait an hour or two, then double-click setup.bat again.
echo If it keeps happening, tell Claude and we will slow the
echo collector down.
goto END

:OFFLINE
echo    Could not reach eBay
echo ==========================================================
echo.
echo The request never got through. Check that this PC is online
echo and can open ebay.com in a browser, then try again.
goto END

:NOLISTINGS
echo    Reached eBay, but read zero listings
echo ==========================================================
echo.
echo The page loaded but nothing was recognised on it. Usually this
echo means the category number needs updating, or eBay changed its
echo page layout.
echo.
echo A copy of the page was saved in the  data\html  folder.
echo Send that file to Claude and it can fix this quickly.
goto END

:UNKNOWN
echo    Stopped with error code !CODE!
echo ==========================================================
echo.
echo Copy the messages above and send them to Claude.
goto END

:NOPYTHON
echo Python is not installed on this PC - or it was installed
echo without ticking the PATH box.
echo.
echo TO FIX:
echo   1. Go to  https://www.python.org/downloads/
echo   2. Click the big yellow "Download Python" button.
echo   3. Run the installer.
echo   4. IMPORTANT: on the very first screen, tick the box at the
echo      bottom that says "Add python.exe to PATH" BEFORE you
echo      click Install Now. This is the step everyone misses.
echo   5. When it finishes, close this window and double-click
echo      setup.bat again.
echo.
pause
exit /b 1

:VENVFAIL
echo Could not build the workspace. Send this window to Claude.
pause
exit /b 1

:INSTALLFAIL
echo The install failed. Details were written to install-log.txt
echo in this folder - send that file to Claude.
pause
exit /b 1

:END
echo.
pause
