@echo off
REM Make a key your other website uses to read the card database.
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"
echo    [ready]

echo.
echo ==========================================================
echo    A key for your website
echo ==========================================================
echo.
echo Your card database is on Cloudflare and it will not answer
echo anyone without a key. This makes one and switches it on.
echo.
echo The key is shown ONCE. Only a scrambled version of it is
echo stored, so it cannot be looked up later - if you lose it,
echo you make another. Copy it somewhere safe when it appears.
echo.
pause

echo.
python -m nflcarddb api-key --label website > "%TEMP%\nflkey.txt" 2>&1
type "%TEMP%\nflkey.txt"

REM Read the hash back out so it can be switched on without anyone
REM copying a 64-character string by hand.
set HASH=
for /f "tokens=2" %%H in ('findstr /b /c:"  hash:" "%TEMP%\nflkey.txt"') do set HASH=%%H
if "%HASH%"=="" goto NOHASH

echo.
echo ==========================================================
echo    Switching it on
echo ==========================================================
echo.
call "%~dp0_cloudflare.bat"
if "%CF_ACCOUNT_ID%"=="" goto NOCLOUD

set DBID=a887dd0e-d852-4ebc-98f0-0e01bc82ad0b
python -m nflcarddb d1-push --account-id "%CF_ACCOUNT_ID%" --database-id %DBID% --add-key %HASH%:website
if errorlevel 1 goto PUSHFAILED

del "%TEMP%\nflkey.txt" >nul 2>&1

echo.
echo ==========================================================
echo    Ready - your website can read the cards now
echo ==========================================================
echo.
echo Give your site the key from above and have it call:
echo.
echo   /v1/cards?quality=clean^&sort=rising^&limit=50
echo.
echo with the header:   Authorization: Bearer YOUR-KEY
echo.
echo To confirm it really arrived, run d1-check.bat and look at
echo the  active_keys  line. That number is how many keys the
echo Cloudflare database will accept; if it is 0, no site can
echo read it whatever key you paste in.
echo.
echo A key put into website JavaScript is PUBLIC - anyone can
echo read the page source. If your site calls this from the
echo browser rather than from a server, treat the key as a
echo name badge and not a lock.
echo.
goto END

:NOHASH
echo.
echo Could not read the key back to switch it on. The key above
echo is still valid - activate it with:
echo   python -m nflcarddb d1-push --add-key ^<hash^>:website
goto END

:PUSHFAILED
echo.
echo The key was made but not switched on - the message above
echo says why. Nothing is half-written. Fix that, then run
echo d1-push.bat and the key goes up with the next upload.
goto END

:NOCLOUD
echo.
echo No Cloudflare credentials stored. Run connect-cloudflare.bat
echo once, then this again.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
del "%TEMP%\nflkey.txt" >nul 2>&1
echo.
pause
