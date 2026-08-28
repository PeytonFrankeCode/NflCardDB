@echo off
REM Checks how many sales have a listing photo, and makes the small ones big.
cd /d "%~dp0"

if not exist venv\Scripts\activate.bat goto NOSETUP
call venv\Scripts\activate.bat

echo.
echo ==========================================================
echo    Listing photos
echo ==========================================================
echo.

python -m nflcarddb images --upgrade

echo.
echo Refreshing the dashboard files...
python -m nflcarddb publish

echo.
echo   with_photo - sales that have a picture
echo   coverage   - the same thing as a fraction (1.0 = all of them)
echo   upgraded   - small thumbnails just rewritten to full size
echo.
echo Photos are links to eBay's own images, so nothing was
echo downloaded and nothing takes up space on this PC.
echo.
echo Next: commit and push in GitHub Desktop so the dashboard
echo picks them up, and run d1-push.bat for your website.
goto END

:NOSETUP
echo.
echo Please double-click  setup.bat  first.
echo.

:END
echo.
pause
