@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_local.ps1"
if errorlevel 1 (
    echo.
    echo Cai dat chua thanh cong. Xem thong bao loi o tren.
    pause
    exit /b 1
)
echo.
echo Cai dat hoan tat. Hay mo file MO_TACH_AM_THANH.bat de su dung.
pause
