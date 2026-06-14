@echo off
title FPI Sync Helper - keep this window open
cd /d "%~dp0"
echo ============================================================
echo  FPI Sync Helper
echo ============================================================
echo  The Sync button in FPI_Dashboard.html will work as long as
echo  this window stays open. Close it to stop the helper.
echo ============================================================
echo.

REM Try 'python' first, fall back to 'py' (Windows Python launcher)
where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python fpi_server.py
) else (
    py fpi_server.py
)

echo.
echo Helper stopped. Press any key to close this window.
pause >nul
