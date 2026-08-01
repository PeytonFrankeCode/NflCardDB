@echo off
REM Double-click this file once, to get your computer ready.
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ==========================================================
echo    NflCardDB - one-time setup
echo ==========================================================
echo.

REM ---- 1. Find Python, whether or not it is on PATH --------------------
REM The "Add python.exe to PATH" checkbox is easy to miss, and on some PCs it
REM is not shown at all. So look in the places Python actually installs to,
REM instead of insisting the box was ticked.
REM Every branch resolves to one full path in PYEXE, quoted only at the call
REM site -- a variable that carries its own quotes gets mangled by batch.
set "PYEXE="

REM The py launcher ships with Python and stays on PATH even when python.exe
REM does not, so try it first. It also sidesteps the Microsoft Store stub, which
REM hijacks the name "python" and opens the Store instead of running anything.
REM Asking Python for sys.executable means a failed launch simply leaves PYEXE
REM unset -- the for-loop body never runs.
for /f "delims=" %%p in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%p"
if defined PYEXE goto GOTPYTHON

for /f "delims=" %%p in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%p"
if defined PYEXE goto GOTPYTHON

for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
  if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
)
if defined PYEXE goto GOTPYTHON

for /d %%d in ("%ProgramFiles%\Python3*") do (
  if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
)
if defined PYEXE goto GOTPYTHON

for /d %%d in ("C:\Python3*") do (
  if exist "%%d\python.exe" set "PYEXE=%%d\python.exe"
)
if not defined PYEXE goto NOPYTHON

:GOTPYTHON
for /f "tokens=2" %%v in ('"!PYEXE!" --version 2^>^&1') do set "PYVER=%%v"
echo Found Python !PYVER!
echo   at !PYEXE!

REM Needs 3.10 or newer.
"!PYEXE!" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto OLDPYTHON
echo.

REM ---- 2. Private workspace so nothing else on your PC is touched ------
echo Building a private workspace. This takes a minute or two...
if exist venv goto HAVEVENV
"!PYEXE!" -m venv venv
if errorlevel 1 goto VENVFAIL
:HAVEVENV

call venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul 2>&1
echo Installing the collector...
pip install -e ".[all]" >install-log.txt 2>&1
if errorlevel 1 goto INSTALLFAIL
echo Installed.
echo.

REM eBay refuses plain scripts on many connections, so a real browser engine is
REM installed up front. It is a large download the first time (a few hundred MB)
REM and is skipped automatically on later runs.
echo Setting up the browser engine. First time only - this downloads a
echo few hundred MB, so it can take several minutes. Please wait...
playwright install chromium >>install-log.txt 2>&1
if errorlevel 1 (
  echo   Could not set up the browser engine. Carrying on anyway - the
  echo   simple method may still work. Details are in install-log.txt
) else (
  echo   Browser engine ready.
)
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
echo    eBay turned us away
echo ==========================================================
echo.
echo eBay refused the request, even through a real browser.
echo This is not something you did wrong.
echo.
echo Wait an hour or two, then double-click setup.bat again -
echo this often clears by itself.
echo.
echo If it keeps happening, send the messages above to Claude.
echo A copy of what eBay sent back was saved in  data\html
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
echo ==========================================================
echo    Python is not on this PC yet
echo ==========================================================
echo.
echo I looked everywhere Python normally installs and did not
echo find it, so it has not been installed yet.
echo.
echo TO FIX:
echo   1. Go to  https://www.python.org/downloads/
echo   2. Click the big yellow "Download Python" button.
echo   3. Run the file that downloads.
echo   4. Click "Install Now" and wait.
echo   5. Close this window and double-click setup.bat again.
echo.
echo You do NOT need to hunt for the "Add python.exe to PATH"
echo checkbox. Tick it if you happen to see it, but this script
echo finds Python either way.
echo.
pause
exit /b 1

:OLDPYTHON
echo.
echo ==========================================================
echo    Python !PYVER! is too old
echo ==========================================================
echo.
echo This project needs Python 3.10 or newer.
echo.
echo Install the current version from
echo   https://www.python.org/downloads/
echo then double-click setup.bat again.
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
