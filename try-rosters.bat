@echo off
REM Which combination actually groups the most cards? Measure, do not guess.
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"

echo.
echo ==========================================================
echo    Which setup groups the most cards?
echo ==========================================================
echo.
echo Two separate choices have been getting changed together
echo and then argued about:
echo.
echo   1. eBay's player list, or the one learned from your own
echo      titles?
echo   2. Do eBay's insert names ("Fireworks", "Touchdown
echo      Masters") count as part of a card's identity, or are
echo      they just kept out of the player's name?
echo.
echo Each swap has moved the numbers by about a thousand cards
echo one way or the other, and no run has ever tested one on
echo its own.
echo.
echo So this reads all your titles once for every combination,
echo reports which wins, and switches to it. Four passes, a
echo few minutes.
echo.

nflcarddb try-vocab config\nfl_players.txt config\nfl_players_ebay.txt --apply
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
echo "no roster files found" means neither list exists yet -
echo run names.bat and catalog.bat first.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
