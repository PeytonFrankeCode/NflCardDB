@echo off
REM Which player list actually groups the most cards? Measure, do not guess.
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"

echo.
echo ==========================================================
echo    Which player list groups cards best?
echo ==========================================================
echo.
echo Swapping the learned player list for eBay's 13,838 names
echo lost about a thousand grouped cards. Nothing in that run
echo said which change caused it.
echo.
echo So this reads all your titles once with each list and
echo reports which one groups the most sales onto one card.
echo A few minutes. Nothing is changed permanently - it ends
echo by putting things back the way your config says.
echo.

nflcarddb try-roster config\nfl_players_ebay.txt config\nfl_players.txt none
if errorlevel 1 goto BROKEN

echo.
echo ==========================================================
echo    Send the table above to Claude
echo ==========================================================
goto END

:BROKEN
echo.
echo That did not finish. The message above says why.
echo.
echo "not found" for a list just means you have not built that
echo one - the comparison still runs with the others.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
