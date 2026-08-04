@echo off
REM The whole daily chain, unattended: collect, publish, upload to Cloudflare.
REM
REM No prompts and no pause -- Task Scheduler runs this with nobody watching,
REM so anything that waits for a keypress would hang the task until it is
REM killed four hours later. Everything goes to logs\ instead.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist logs mkdir logs

REM Sortable filename, and one log per day so a week of them stays readable.
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set LDT=%%I
if "!LDT!"=="" set LDT=00000000000000
set STAMP=!LDT:~0,4!-!LDT:~4,2!-!LDT:~6,2!
set LOG=logs\daily-!STAMP!.log

call :log "=== run started !LDT:~8,2!:!LDT:~10,2! ==="

if not exist venv\Scripts\activate.bat (
  call :log "FAILED: no venv -- run setup.bat once on this PC"
  exit /b 2
)
call venv\Scripts\activate.bat

REM ---- collect -------------------------------------------------------------
call :log "collecting..."
nflcarddb -v scrape --save-html data\html >> "!LOG!" 2>&1
set CODE=!errorlevel!

if "!CODE!"=="8" (
  call :log "STOPPED: eBay session expired -- double-click login.bat once"
  exit /b 8
)
if "!CODE!"=="4" (
  call :log "STOPPED: eBay asked for a human check. Partial data was saved; tomorrow's run continues."
  exit /b 4
)
if "!CODE!"=="5" (
  call :log "STOPPED: could not reach eBay -- offline?"
  exit /b 5
)
if not "!CODE!"=="0" (
  call :log "STOPPED: scrape exited !CODE!"
  exit /b !CODE!
)

REM ---- catch up on missing days -------------------------------------------
REM One task, not two: the collector holds a lock on its Chrome profile, so a
REM separate backfill task running at the same time would just fail on it.
REM
REM Bounded by wall clock so this ends before the PC is wanted for something
REM else. Days already collected are skipped, so it walks backwards through
REM eBay's 90-day window a few days a night and then becomes a no-op.
if "%NFLCARDDB_CATCHUP_MINUTES%"=="" set NFLCARDDB_CATCHUP_MINUTES=180
if "%NFLCARDDB_CATCHUP_MINUTES%"=="0" goto SKIPCATCHUP

call :log "catching up on older days (up to !NFLCARDDB_CATCHUP_MINUTES! minutes)..."
nflcarddb -v backfill --days 90 --max-minutes !NFLCARDDB_CATCHUP_MINUTES! >> "!LOG!" 2>&1
set BCODE=!errorlevel!
if "!BCODE!"=="8" call :log "catch-up stopped: session expired -- run login.bat"
if "!BCODE!"=="4" call :log "catch-up stopped: eBay human check. Days already collected are kept."
goto CATCHUPDONE

:SKIPCATCHUP
call :log "catch-up disabled (NFLCARDDB_CATCHUP_MINUTES=0)"

:CATCHUPDONE
nflcarddb coverage >> "!LOG!" 2>&1

REM ---- photos + dashboard files -------------------------------------------
call :log "sizing photos and refreshing the dashboard..."
nflcarddb images --upgrade >> "!LOG!" 2>&1
nflcarddb publish >> "!LOG!" 2>&1

REM ---- upload to Cloudflare ------------------------------------------------
REM Skipped rather than prompted when no credentials are stored: an unattended
REM run has nobody to answer a prompt.
if "%CLOUDFLARE_API_TOKEN%"=="" goto NOCLOUD
if "%CF_ACCOUNT_ID%"=="" goto NOCLOUD

set DBID=a887dd0e-d852-4ebc-98f0-0e01bc82ad0b
call :log "uploading to Cloudflare..."
nflcarddb d1-push --account-id "%CF_ACCOUNT_ID%" --database-id %DBID% --schema api\schema.sql --schema-only >> "!LOG!" 2>&1
nflcarddb d1-push --account-id "%CF_ACCOUNT_ID%" --database-id %DBID% >> "!LOG!" 2>&1
if errorlevel 1 (
  call :log "WARNING: upload failed -- the data is safe on this PC, see above"
) else (
  call :log "uploaded"
)
goto DONE

:NOCLOUD
call :log "skipped Cloudflare upload -- no credentials stored (run connect-cloudflare.bat once)"

:DONE
nflcarddb stats >> "!LOG!" 2>&1
call :log "=== run finished ==="
exit /b 0

:log
echo %~1
echo [%TIME%] %~1 >> "!LOG!"
goto :eof
