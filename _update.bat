@echo off
REM Shared helper: is this folder behind GitHub, and do you want to catch up?
REM
REM Called at the start of the tools that report numbers, because an out-of-date
REM checkout produces a report that looks fine and describes old code. That has
REM now cost two rounds of diagnosing a fix that was never on the machine.
REM
REM Finds git without needing it on PATH: GitHub Desktop ships its own copy and
REM does not add it, which is the normal state on this PC.

set "GIT="
where git >nul 2>&1 && set "GIT=git"
if not defined GIT (
  for /d %%D in ("%LOCALAPPDATA%\GitHubDesktop\app-*") do (
    if exist "%%D\resources\app\git\cmd\git.exe" set "GIT=%%D\resources\app\git\cmd\git.exe"
  )
)
if not defined GIT (
  if exist "%ProgramFiles%\Git\cmd\git.exe" set "GIT=%ProgramFiles%\Git\cmd\git.exe"
)

REM No git found: not a problem worth stopping for, just skip the check.
if not defined GIT goto :eof

"%GIT%" fetch --quiet origin 2>nul
if errorlevel 1 goto :eof

REM Via a temp file rather than `for /f`, which needs escaped quoting that
REM breaks the moment GIT holds a path with spaces -- which it does, because
REM GitHub Desktop lives under "Program Files" or a user's AppData.
set BEHIND=0
"%GIT%" rev-list --count HEAD..@{u} > "%TEMP%\nflcarddb-behind.txt" 2>nul
if exist "%TEMP%\nflcarddb-behind.txt" set /p BEHIND=<"%TEMP%\nflcarddb-behind.txt"
del "%TEMP%\nflcarddb-behind.txt" >nul 2>&1

if not defined BEHIND goto :eof
if "%BEHIND%"=="0" goto :eof

echo.
echo ==========================================================
echo    THIS FOLDER IS %BEHIND% UPDATE(S) BEHIND
echo ==========================================================
echo.
echo Running now would report on the old code, and the numbers
echo would look perfectly normal while describing nothing you
echo just changed.
echo.
set /p DOPULL=Update now? (y/n):
if /i not "%DOPULL%"=="y" goto SKIPPED

echo.
REM No refspec: pull from the branch this one already tracks. Naming a branch
REM here would be wrong the moment the working branch changes.
"%GIT%" pull --ff-only
if errorlevel 1 goto CONFLICT
echo.
echo Updated. Carrying on.
echo.
goto :eof

:CONFLICT
echo.
echo ----------------------------------------------------------
echo    Could not update automatically
echo ----------------------------------------------------------
echo.
echo Usually this means you have your own changes here that are
echo not committed yet. Open GitHub Desktop, commit them, then
echo click Pull origin.
echo.
echo Nothing was changed. Continuing with the current code.
echo.
pause
goto :eof

:SKIPPED
echo.
echo Continuing with the current code. The report will describe
echo that, not the latest version.
echo.
