@echo off
REM Writes a diagnostic file and opens it. Deliberately does everything the
REM other tools do NOT: no update check, no venv assumption, no early exit,
REM and every single line captured to a file rather than a window.
REM
REM A window that shows nothing is the one failure the other scripts cannot
REM report on, because whatever went wrong took the window with it. A file
REM survives that.

cd /d "%~dp0"
set "LOG=%~dp0diagnostic.txt"

echo NflCardDB diagnostic > "%LOG%"
echo Generated: %DATE% %TIME% >> "%LOG%"
echo Folder: %~dp0 >> "%LOG%"
echo. >> "%LOG%"

echo ---- does the project look present ---- >> "%LOG%"
if exist "setup.bat" (echo setup.bat: yes >> "%LOG%") else (echo setup.bat: MISSING >> "%LOG%")
if exist "src\nflcarddb\cli.py" (echo source: yes >> "%LOG%") else (echo source: MISSING >> "%LOG%")
if exist "config\queries.yml" (echo config: yes >> "%LOG%") else (echo config: MISSING >> "%LOG%")
if exist "venv\Scripts\activate.bat" (echo venv: yes >> "%LOG%") else (echo venv: MISSING - run setup.bat >> "%LOG%")
if exist "data\nflcarddb.sqlite" (echo database: yes >> "%LOG%") else (echo database: MISSING >> "%LOG%")
echo. >> "%LOG%"

echo ---- config contents ---- >> "%LOG%"
if exist "config\queries.yml" type "config\queries.yml" >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo ---- activating venv ---- >> "%LOG%"
if not exist "venv\Scripts\activate.bat" goto NOVENV
call "venv\Scripts\activate.bat" >> "%LOG%" 2>&1
echo activate exit code: %ERRORLEVEL% >> "%LOG%"
echo. >> "%LOG%"

echo ---- python ---- >> "%LOG%"
python -V >> "%LOG%" 2>&1
echo python exit code: %ERRORLEVEL% >> "%LOG%"
echo. >> "%LOG%"

echo ---- can the package be imported ---- >> "%LOG%"
python -c "import nflcarddb, sys; print('package ok'); print(sys.executable)" >> "%LOG%" 2>&1
echo import exit code: %ERRORLEVEL% >> "%LOG%"
echo. >> "%LOG%"

echo ---- parser version ---- >> "%LOG%"
python -c "from nflcarddb.parse_title import PARSER_VERSION as v; print(v)" >> "%LOG%" 2>&1
echo. >> "%LOG%"

echo ---- nflcarddb command ---- >> "%LOG%"
python -m nflcarddb --help >> "%LOG%" 2>&1
echo help exit code: %ERRORLEVEL% >> "%LOG%"
echo. >> "%LOG%"

REM The generated launcher, recorded separately and NOT relied on. It is an
REM unsigned .exe that pip writes into the venv, and a machine running Device
REM Guard refuses to start it -- which reads as "blocked by your organization's
REM policy" from a script that otherwise looks fine. Everything above runs the
REM module through python.exe instead, so this line is diagnosis, not a test.
echo ---- generated launcher (not used) ---- >> "%LOG%"
where nflcarddb >> "%LOG%" 2>&1
nflcarddb --help >nul 2>>"%LOG%"
echo launcher exit code: %ERRORLEVEL% >> "%LOG%"
echo. >> "%LOG%"

echo ---- gaps, in full ---- >> "%LOG%"
python -m nflcarddb gaps --limit 5 >> "%LOG%" 2>&1
echo gaps exit code: %ERRORLEVEL% >> "%LOG%"
echo. >> "%LOG%"

echo ---- audit, in full ---- >> "%LOG%"
python -m nflcarddb audit >> "%LOG%" 2>&1
echo audit exit code: %ERRORLEVEL% >> "%LOG%"
echo. >> "%LOG%"
goto DONE

:NOVENV
echo Cannot go further: no venv. Run setup.bat first. >> "%LOG%"

:DONE
echo. >> "%LOG%"
echo ---- end ---- >> "%LOG%"

echo.
echo ==========================================================
echo    Wrote diagnostic.txt
echo ==========================================================
echo.
echo Everything that happened is in that file, including any
echo error that would normally vanish with the window.
echo.
echo Opening it now. Send the whole thing to Claude.
echo.
start "" notepad "%LOG%"
pause
