@echo off
REM Shared helper: say whether this folder is behind GitHub, and fast-forward
REM if that is safe. Called at the start of the tools that report numbers,
REM because an out-of-date checkout produces a report that looks fine and
REM describes old code.
REM
REM It never asks a question and never pauses. An earlier version did both,
REM and a helper that can block the tool it is helping is a bad helper: the
REM config files these tools write are tracked, so a fast-forward legitimately
REM fails, and the failure branch then sat waiting for a keypress in front of
REM every report.
REM
REM Set NFLCARDDB_SKIP_UPDATE=1 to bypass it entirely.

if not "%NFLCARDDB_SKIP_UPDATE%"=="" goto :eof

REM Find git without needing it on PATH: GitHub Desktop ships its own copy and
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
echo    ^>^> This folder is %BEHIND% update^(s^) behind GitHub. Catching up...
"%GIT%" pull --ff-only >nul 2>&1
if errorlevel 1 goto MANUAL
echo    ^>^> Updated.
echo.
goto :eof

:MANUAL
echo    ^>^> Could not update automatically - usually your own uncommitted
echo    ^>^> changes. Commit and Pull in GitHub Desktop when convenient.
echo    ^>^> Carrying on with the code that is here.
echo.
goto :eof
