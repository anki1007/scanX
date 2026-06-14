@echo off
REM One-time installer for the FPI Dashboard sync helper.
REM Double-click this file. After it finishes, the Sync button works forever.
title FPI Sync Helper - One-Time Installer
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_sync_helper.ps1" %*
if errorlevel 1 (
    echo.
    echo Installer reported an error. See messages above.
    pause
)
