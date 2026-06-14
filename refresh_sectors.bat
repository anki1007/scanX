@echo off
title scanX - refresh sectors (one-off)
cd /d "%~dp0"
echo.
echo  Refreshing all 22 sector/industry tailwind scores + per-sector drill-down (~2-3 min)...
echo  (Best run when scanX.bat is NOT mid-crawl, to avoid Screener rate limits.)
echo.
rem --- locate a WORKING Python 3 (skips broken py-launcher entries like C:\Python314) ---
set "PYEXE="
for %%V in (-3.12 -3.11 -3.13 -3.10) do call :trypy py %%V
call :trypy python
call :trypy python3
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE (
  echo  No working Python 3 found. Run scanX.bat once ^(it locates Python^), or install from python.org.
  pause >nul & exit /b 1
)
echo  Using Python: %PYEXE%
echo.
%PYEXE% scripts\refresh_sectors.py %*
echo.
echo  Done. Hard-refresh the Sector tab (Ctrl+Shift+R) to see all 22 sectors.
echo  Press any key to close.
pause >nul
exit /b

:trypy
if defined PYEXE goto :eof
%* -c "import sys; sys.exit(0 if sys.version_info[:2]>=(3,9) else 1)" >nul 2>nul && set "PYEXE=%*"
goto :eof
