@echo off
REM Shared by every script that talks to Cloudflare: load the token and account
REM ID, asking only if they are genuinely not known yet, and remember them.
REM
REM Not called directly -- the other .bat files `call` it.
REM
REM Why a file rather than setx alone: setx writes to the user environment, but
REM a window that is already open never sees it, and a fresh one only picks it
REM up sometimes depending on how it was launched. That made "I already gave it
REM the token" and "it keeps asking" both true at once. A file read at the top
REM of every script is boring and works.

set "CREDFILE=%~dp0data\cloudflare.txt"

REM Already in the environment? Nothing to do.
if not "%CLOUDFLARE_API_TOKEN%"=="" if not "%CF_ACCOUNT_ID%"=="" goto :eof

REM Otherwise read what was saved last time.
if exist "%CREDFILE%" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%CREDFILE%") do (
    if "%%A"=="token" set "CLOUDFLARE_API_TOKEN=%%B"
    if "%%A"=="account" set "CF_ACCOUNT_ID=%%B"
  )
)

if not "%CLOUDFLARE_API_TOKEN%"=="" if not "%CF_ACCOUNT_ID%"=="" (
  echo Using the Cloudflare details saved on this PC.
  echo.
  goto :eof
)

echo.
echo This needs your Cloudflare details. It will remember them,
echo so this is the last time you are asked.
echo.

if "%CLOUDFLARE_API_TOKEN%"=="" (
  echo API token - create one at:
  echo   https://dash.cloudflare.com/profile/api-tokens
  echo   Create Token  -^>  Custom token  -^>  Account ^| D1 ^| Edit
  echo.
  set /p CFTOKEN=Paste your API token:
  echo.
)
if "%CF_ACCOUNT_ID%"=="" (
  echo Account ID - the long value on the right of your Cloudflare
  echo overview page at https://dash.cloudflare.com/
  echo.
  set /p CFACCT=Paste your Account ID:
  echo.
)

if not "%CFTOKEN%"=="" set "CLOUDFLARE_API_TOKEN=%CFTOKEN%"
if not "%CFACCT%"=="" set "CF_ACCOUNT_ID=%CFACCT%"

if not exist "%~dp0data" mkdir "%~dp0data"
> "%CREDFILE%" echo token=%CLOUDFLARE_API_TOKEN%
>> "%CREDFILE%" echo account=%CF_ACCOUNT_ID%

REM Also into the user environment, so a command prompt opened later has them.
setx CLOUDFLARE_API_TOKEN "%CLOUDFLARE_API_TOKEN%" >nul 2>&1
setx CF_ACCOUNT_ID "%CF_ACCOUNT_ID%" >nul 2>&1

echo Saved. You will not be asked again.
echo (Stored in data\cloudflare.txt, which git ignores, so it
echo  cannot be committed by accident.)
echo.
goto :eof
