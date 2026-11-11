@echo off
cd /d "%~dp0"
set "TACH_PYTHON=%~dp0.runtime\venv\Scripts\python.exe"
if not exist "%TACH_PYTHON%" (
    echo Chua tim thay moi truong. Hay chay CAI_DAT.bat truoc.
    pause
    exit /b 1
)
"%TACH_PYTHON%" -m tach_am_thanh gui
if errorlevel 1 (
    echo.
    echo Khong the mo tool. Hay xem thong bao loi o tren.
    pause
    exit /b 1
)
