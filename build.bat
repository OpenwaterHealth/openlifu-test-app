@echo off
echo OpenLIFU Test Application - Build Script
echo ========================================
echo.

:: Check if running in virtual environment
if defined VIRTUAL_ENV (
    echo [INFO] Virtual environment detected: %VIRTUAL_ENV%
) else (
    echo [WARNING] No virtual environment detected. Consider using one.
    echo You can create one with: python -m venv .venv
    echo Then activate it with: .venv\Scripts\activate
)

echo.
echo Running Python build script...
python build.py

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ================================
    echo Build completed successfully!
    echo ================================
    echo.
    echo The executable is located in: dist\OpenLIFU-TestApp\
    echo You can run it directly or use the launcher: dist\OpenLIFU-TestApp\launch.bat
    echo.
    pause
) else (
    echo.
    echo ================================
    echo Build failed with error code: %ERRORLEVEL%
    echo ================================
    echo.
    pause
)