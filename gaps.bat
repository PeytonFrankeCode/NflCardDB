@echo off
REM What the parser cannot account for, ranked by how often it appears.
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat
call "%~dp0_update.bat"

echo.
echo ==========================================================
echo    What the parser does not recognise
echo ==========================================================
echo.
echo Words like "Dragonscale" and "Decade Dominance" are in no
echo list, so they get read as part of the player's name and
echo the card comes out described wrongly.
echo.
echo Finding them by reading through mistakes one at a time is
echo how a dozen names got added per round while thousands of
echo listings stayed wrong.
echo.
echo This lists every word left over after everything the
echo parser knows, ranked by how many sales it appears in. The
echo top of the list is what is costing you the most.
echo.

nflcarddb gaps --limit 60
if errorlevel 1 goto BROKEN

echo.
echo ==========================================================
echo    Send the list above to Claude
echo ==========================================================
goto END

:BROKEN
echo.
echo That did not finish. The message above says why.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
