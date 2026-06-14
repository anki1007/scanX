@echo off
setlocal EnableExtensions
title scanX - NSE / BSE terminal
cd /d "%~dp0"

REM =====================================================================
REM  scanX.bat  -  one launcher that does everything
REM  ---------------------------------------------------------------------
REM   1. builds a local Python environment (first run only)
REM   2. installs the Scrapling web-scraping engine (anki1007 fork) +
REM      the Camoufox stealth browser, so scanX can scrape BSE/NSE/Screener
REM   3. loads your Screener.in login (credentials.bat)
REM   4. can refresh every board + bake fundamentals
REM   5. runs the LIVE local API + dashboard  -> any of ~5,000 stocks
REM   6. can deploy the static site to GitHub Pages (git push docs)
REM
REM  Put this file in the scanX repo ROOT (next to scripts\ and docs\).
REM  Just double-click it. Press Enter at the menu to start the terminal.
REM =====================================================================

set "PORT=8777"
set "VENV=.venv"
set "PYEXE=%VENV%\Scripts\python.exe"
set "SCRAPLING_FORK=git+https://github.com/anki1007/Scrapling.git"

echo.
echo  ============================================
echo            s c a n X   launcher
echo  ============================================

REM ---- 1. find a Python to bootstrap the venv -------------------------
set "BOOT="
where py     >nul 2>&1 && set "BOOT=py -3"
if not defined BOOT ( where python >nul 2>&1 && set "BOOT=python" )
if not defined BOOT (
  echo [scanX] Python 3 was not found on PATH.
  echo         Install Python 3.10+ from https://www.python.org/downloads/
  echo         tick "Add python.exe to PATH" during setup, then re-run scanX.bat
  echo.
  pause
  exit /b 1
)

REM ---- 2. create venv + install deps on first run --------------------
if not exist "%PYEXE%" (
  echo [scanX] First run - creating local environment in %VENV% ...
  %BOOT% -m venv "%VENV%"
  if errorlevel 1 ( echo [scanX] Could not create the virtual environment. & pause & exit /b 1 )
)
if not exist "%VENV%\.scanx_ready"        call :install_deps
if not exist "%VENV%\.scanx_scraper_ready" call :install_scraper

REM ---- 3. load Screener credentials (optional, for live data) --------
if exist "credentials.bat" (
  call "credentials.bat"
) else (
  if not defined SCREENER_SESSIONID if not defined SCREENER_EMAIL call :make_cred_template
)

REM ---- 4. refresh the Screener login session (best effort) ----------
echo [scanX] Checking Screener.in session ...
"%PYEXE%" scripts\screener_login.py 2>nul

REM ====================== MENU =======================================
:menu
echo.
echo  ================== what do you want to do? ==================
echo    [1]  Start scanX terminal   (live API + open dashboard)   ^<- default
echo    [2]  Refresh ALL boards, then start
echo    [3]  Refresh + bake fundamentals (wide ~1500), then start
echo    [4]  Deploy static site to GitHub   (git add/commit/push docs)
echo    [5]  Repair / reinstall the Python environment
echo    [6]  Reinstall the scraping engine (Scrapling fork + Camoufox)
echo    [0]  Exit
echo  ============================================================
set "CH="
set /p "CH=Choose 1-6 (Enter = 1): "
if not defined CH set "CH=1"
if "%CH%"=="1" goto serve
if "%CH%"=="2" goto refresh_then_serve
if "%CH%"=="3" goto bake_then_serve
if "%CH%"=="4" goto deploy
if "%CH%"=="5" goto repair
if "%CH%"=="6" goto repair_scraper
if "%CH%"=="0" goto end
echo [scanX] Please type a number 0-6.
goto menu

:refresh_then_serve
call :refresh_all
goto serve

:bake_then_serve
call :refresh_all
call :bake_wide
goto serve

REM ====================== START THE TERMINAL =========================
:serve
echo.
echo [scanX] Starting local server on http://localhost:%PORT%/  (live API enabled)
start "scanX local server" "%PYEXE%" scripts\serve.py --port %PORT% --host 127.0.0.1
REM let the server bind, then open the dashboard in the default browser
timeout /t 3 >nul
start "" "http://localhost:%PORT%/"
echo.
echo [scanX] Dashboard opened in your browser.
echo        The server is running in a separate "scanX local server" window.
echo        Press any key HERE to stop scanX and close the server.
pause >nul
taskkill /FI "WINDOWTITLE eq scanX local server*" /T /F >nul 2>&1
echo [scanX] Stopped.
goto end

REM ====================== REFRESH ALL BOARDS =========================
:refresh_all
echo.
echo [scanX] Refreshing all boards from Screener / NSE / BSE (a few minutes) ...
"%PYEXE%" scripts\refresh_scanx.py --pages 8
"%PYEXE%" scripts\refresh_orders.py --months 3
"%PYEXE%" scripts\refresh_buybacks.py --months 12
"%PYEXE%" scripts\refresh_special.py
"%PYEXE%" scripts\refresh_fii.py
"%PYEXE%" scripts\refresh_marketpulse.py
"%PYEXE%" scripts\refresh_fpi.py
"%PYEXE%" scripts\refresh_sectors.py
"%PYEXE%" scripts\refresh_technofunda.py --mcap-floor 5 --screen-pages 250
"%PYEXE%" scripts\refresh_magicformula.py --screen-pages 250
"%PYEXE%" scripts\refresh_iv.py --per-sector 30
echo [scanX] Board refresh complete.
exit /b 0

REM ====================== BAKE FUNDAMENTALS (WIDE) ===================
:bake_wide
echo.
echo [scanX] Baking fundamental bundles for the wide universe.
echo        (This is the slow one - Screener throttles, so it may take a while.
echo         It is resumable: re-run any time, already-baked stocks are skipped.)
"%PYEXE%" scripts\refresh_fundamentals.py --top 1500 --skip-existing
echo [scanX] Bake complete -> docs\data\fundamental\
exit /b 0

REM ====================== DEPLOY TO GITHUB ===========================
:deploy
echo.
where git >nul 2>&1
if errorlevel 1 ( echo [scanX] git is not on PATH - install Git for Windows first. & goto menu )
echo [scanX] Committing and pushing docs\ to GitHub Pages ...
git add -A docs
git commit -m "data: manual scanX refresh"
if errorlevel 1 echo [scanX] Nothing new to commit.
git push
if errorlevel 1 echo [scanX] Push failed - check your remote / GitHub auth.
echo [scanX] Deploy step finished.
goto menu

REM ====================== ENV HELPERS ================================
:repair
echo [scanX] Reinstalling the core Python environment ...
del "%VENV%\.scanx_ready" 2>nul
call :install_deps
goto menu

:repair_scraper
echo [scanX] Reinstalling the scraping engine ...
del "%VENV%\.scanx_scraper_ready" 2>nul
call :install_scraper
goto menu

:install_deps
echo [scanX] Installing core Python packages (lean, reliable set) ...
"%PYEXE%" -m pip install --upgrade pip >nul 2>&1
"%PYEXE%" -m pip install pandas numpy requests beautifulsoup4 lxml curl_cffi yfinance pdfplumber openpyxl pyotp
if errorlevel 1 (
  echo [scanX] Some core packages failed to install - live features may be limited.
) else (
  > "%VENV%\.scanx_ready" echo ready
  echo [scanX] Core environment ready.
)
exit /b 0

:install_scraper
echo.
echo [scanX] Installing the Scrapling web-scraping engine (anki1007 fork) ...
echo        Source: %SCRAPLING_FORK%
"%PYEXE%" -m pip install "%SCRAPLING_FORK%" playwright curl_cffi
if errorlevel 1 (
  echo [scanX] Scrapling fork install failed - scraping falls back to curl_cffi / requests.
  echo        You can retry later from the menu, option 6.
  exit /b 0
)
echo [scanX] Downloading the Camoufox stealth browser (one-time, ~150 MB) ...
"%PYEXE%" -m scrapling install
"%PYEXE%" -m playwright install chromium
> "%VENV%\.scanx_scraper_ready" echo ready
echo [scanX] Scraping engine ready  (Scrapling fork + Camoufox + curl_cffi).
exit /b 0

:make_cred_template
> "credentials.bat" echo @echo off
>>"credentials.bat" echo REM === scanX Screener.in login (needed only for LIVE lookups) ===
>>"credentials.bat" echo REM Option A (recommended): your Screener email + password
>>"credentials.bat" echo set "SCREENER_EMAIL=you@example.com"
>>"credentials.bat" echo set "SCREENER_PASSWORD=your-password-here"
>>"credentials.bat" echo REM Option B: instead paste a sessionid cookie from screener.in
>>"credentials.bat" echo REM set "SCREENER_SESSIONID=paste-cookie-here"
echo [scanX] Created credentials.bat - open it, add your Screener login, then re-run.
echo        (Static boards work without it; live any-stock lookup needs it.)
exit /b 0

:end
endlocal
exit /b 0
