@echo off
title scanX
cd /d "%~dp0"
set "SCANX_ROOT=%~dp0"
set "_ps=%TEMP%\scanX_run.ps1"
powershell -NoProfile -Command "$l=Get-Content -LiteralPath '%~f0'; $i=($l | Select-String -Pattern '^#PSBODY#$' | Select-Object -First 1).LineNumber; $l | Select-Object -Skip $i | Set-Content -LiteralPath '%_ps%'"
powershell -NoProfile -ExecutionPolicy Bypass -File "%_ps%" %*
del "%_ps%" 2>nul
echo.
echo Stopped. Press any key to close.
pause >nul
exit /b
#PSBODY#
# ====================================================================
#  scanX - ONE file does everything.  Double-click = LIVE dashboard.
#    scanX.bat            -> LIVE (login, scrape, serve, publish loop)
#    scanX.bat -Publish   -> commit + push to GitHub + open Pages settings
#    scanX.bat -Reset     -> wipe git history + clean force-push (rare)
#    scanX.bat -NoPush    -> LIVE but don't push to GitHub
#    scanX.bat -NoRealtime-> LIVE but skip the intraday engine
# ====================================================================
param([int]$Port = 8777, [switch]$NoPush, [switch]$NoRealtime, [switch]$Reset, [switch]$Publish)
$ErrorActionPreference = 'Continue'
$Root = $env:SCANX_ROOT; if ($Root) { $Root = $Root.TrimEnd('\') }
if (-not $Root) { $Root = Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location $Root
$env:PYTHONIOENCODING = 'utf-8'
$env:SCANX_NO_DHAN = '1'    # Dhan removed: NSE/BSE delayed quotes everywhere
$Repo = "https://github.com/anki1007/scanX.git"

function Find-Python {
  function Test-Py($exe, $rest) {
    try { $v = & $exe @rest --version 2>&1
      if ($LASTEXITCODE -eq 0 -and "$v" -match 'Python 3\.(9|1[0-9])') { return $true } } catch {}
    return $false
  }
  foreach ($spec in @('py|-3.12','py|-3.11','py|-3.13','py|-3.10','py|-3','python','python3')) {
    $p = $spec -split '\|'; $exe = $p[0]; $rest = @(); if ($p.Count -gt 1) { $rest = $p[1..($p.Count-1)] }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    if (Test-Py $exe $rest) { return ,(@($exe) + $rest) }
  }
  $paths = @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
             "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
             "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
             'C:\Python312\python.exe','C:\Python311\python.exe','C:\Python313\python.exe')
  foreach ($base in @('C:\', "$env:LOCALAPPDATA\Programs\Python")) {
    if (Test-Path $base) {
      Get-ChildItem -Path $base -Filter python.exe -Depth 1 -ErrorAction SilentlyContinue |
        ForEach-Object { $paths += $_.FullName } }
  }
  foreach ($exe in ($paths | Select-Object -Unique)) {
    if ((Test-Path $exe) -and (Test-Py $exe @())) { return ,@($exe) } }
  throw "No working Python 3 found. Install from https://www.python.org/downloads/ (tick 'Add to PATH')."
}

$py = Find-Python; $exe = $py[0]; $pre = @(); if ($py.Count -gt 1) { $pre = $py[1..($py.Count-1)] }
if (Test-Path "$Root\credentials.ps1") { . "$Root\credentials.ps1" }

# ------------------------------------------------------------------ RESET
if ($Reset) {
  Write-Host ("=" * 66) -ForegroundColor Green
  Write-Host " scanX - CLEAN reset + push ($Repo)" -ForegroundColor Green
  Write-Host ("=" * 66) -ForegroundColor Green
  Write-Host "Close any running scanX (LIVE) window first." -ForegroundColor Yellow
  Read-Host "Press Enter to continue (or close this window to abort)"
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Host "Git not installed." -ForegroundColor Red; return }
  & $exe @pre (Join-Path $Root 'scripts\refresh_scanx.py') --pages 8
  if (Test-Path "$Root\.git") { Remove-Item -Recurse -Force "$Root\.git" }
  git init -b main | Out-Null
  git config core.autocrlf false
  git config user.email "scanx@users.noreply.github.com"
  git config user.name  "scanX"
  git add -A
  $leak = git diff --cached --name-only |
    Select-String -Pattern 'credentials\.ps1|(^|/)dhan/|(^|/)kite/|dhan_token|kite_token|screener_session|_credentials\.txt|secret|\.pem$|\.key$|\.env$|api_key|gemin'
  if ($leak) { Write-Host "ABORT - sensitive files staged, nothing pushed:" -ForegroundColor Red
    $leak | ForEach-Object { Write-Host "   $_" -ForegroundColor Red }; return }
  git commit -m "scanX clean snapshot" | Out-Null
  git remote add origin $Repo
  git push -u origin main --force
  $base = $Repo -replace '\.git$', ''; Start-Process "$base/settings/pages"
  Write-Host "Pushed. In Pages: Source=Deploy from a branch, Branch=main, Folder=/docs -> Save" -ForegroundColor Green
  return
}

# ------------------------------------------------------------------ PUBLISH
if ($Publish) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Host "Git not installed." -ForegroundColor Red; return }
  & $exe @pre (Join-Path $Root 'scripts\refresh_scanx.py') --pages 8
  if (-not (Test-Path .git)) { git init | Out-Null }
  git config core.autocrlf false
  if (-not (git config user.email)) { git config user.email "scanx@users.noreply.github.com" }
  if (-not (git config user.name))  { git config user.name  "scanX" }
  git add -A; git commit -m "scanX update" 2>$null | Out-Null
  git branch -M main 2>$null
  if (@(git remote) -contains 'origin') { git remote set-url origin $Repo } else { git remote add origin $Repo }
  # the cloud workflows also push (quotes every 15 min) — integrate remote
  # commits first, keeping LOCAL changes on any conflict, then push with retry.
  # If rebase can't resolve (e.g. files deleted on the remote by a bad push),
  # fall back to a merge where the LOCAL disk wins everything — that also
  # restores any files missing on GitHub.
  $pushed = $false
  foreach ($try in 1..3) {
    git push -u origin main 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
    git fetch origin main 2>$null
    git rebase -X theirs origin/main 2>$null
    if ($LASTEXITCODE -ne 0) {
      git rebase --abort 2>$null
      Write-Host "      rebase blocked - merging with LOCAL-WINS (repairs remote)..." -ForegroundColor Yellow
      git merge origin/main --no-commit 2>$null | Out-Null
      git checkout --ours -- . 2>$null
      git add -A 2>$null
      git commit -m "merge remote - local wins (restore full tree)" 2>$null | Out-Null
    }
  }
  if ($pushed) {
    Start-Process "https://anki1007.github.io/scanX/"
    Write-Host "Pushed. Live at https://anki1007.github.io/scanX/ in ~1 min." -ForegroundColor Green
  } else {
    Write-Host "PUSH FAILED after retries - check network/credentials and re-run publish.bat" -ForegroundColor Red
  }
  return
}

# ================================================================== LIVE (default)
Write-Host ("=" * 70) -ForegroundColor Green
Write-Host " scanX LIVE  -  auto setup / login / scrape / serve / publish" -ForegroundColor Green
Write-Host ("=" * 70) -ForegroundColor Green
if (Test-Path "$Root\credentials.ps1") { Write-Host "[1/6] credentials loaded" -ForegroundColor Cyan }
else { Write-Host "[1/6] credentials.ps1 missing - copy credentials.example.ps1" -ForegroundColor Yellow }
Write-Host "[2/6] Python: " -NoNewline -ForegroundColor Cyan; & $exe @pre --version

$marker = Join-Path $Root '.deps_ok'; $req = Join-Path $Root 'requirements.txt'
$need = -not (Test-Path $marker)
if ((Test-Path $marker) -and (Test-Path $req) -and ((Get-Item $req).LastWriteTime -gt (Get-Item $marker).LastWriteTime)) { $need = $true }
if ($need) {
  Write-Host "[3/6] installing/updating dependencies..." -ForegroundColor Cyan
  & $exe @pre -m pip install -r $req --quiet --disable-pip-version-check 2>$null
  & $exe @pre -m scrapling install 2>$null
  New-Item -ItemType File $marker -Force | Out-Null
} else { Write-Host "[3/6] dependencies ready" -ForegroundColor Cyan }

# Dhan dependency REMOVED by request: quotes come from the free NSE/BSE
# delayed feed (~1-3 min) — no broker account, no daily token, no TOTP.
$env:SCANX_NO_DHAN = '1'
Write-Host "[4/6] quotes: NSE/BSE delayed feed (~1-3 min) - no broker login needed" -ForegroundColor Cyan

# git self-heal: a crashed run can leave a stale lock / corrupt index / bad
# multi-pack-index, which makes every publish silently fail. Detect + repair.
if ((Test-Path .git) -and (Get-Command git -ErrorAction SilentlyContinue)) {
  # a stale index.lock blocks every add/commit WITHOUT making `git status` fail,
  # so remove it explicitly first (only if old enough to be from a dead process)
  $lock = ".git\index.lock"
  if ((Test-Path $lock) -and (((Get-Date) - (Get-Item $lock).LastWriteTime).TotalMinutes -gt 5)) {
    Remove-Item -Force $lock -ErrorAction SilentlyContinue
    Write-Host "      removed stale git index.lock (was blocking publishes)" -ForegroundColor Yellow
  }
  $gitErr = (git status --porcelain 2>&1) | Out-String
  if ($gitErr -match 'error:|fatal:') {
    Write-Host "      git repo unhealthy - auto-repairing index/aux files..." -ForegroundColor Yellow
    Remove-Item -Force ".git\index.lock", ".git\index", ".git\objects\pack\multi-pack-index",
                       ".git\objects\info\commit-graph" -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force ".git\objects\info\commit-graphs" -ErrorAction SilentlyContinue
    git read-tree HEAD 2>$null
    $gitErr2 = (git status --porcelain 2>&1) | Out-String
    if ($gitErr2 -match 'error:|fatal:') { Write-Host "      git still unhealthy - publishing may fail (data stays local-good)" -ForegroundColor Red }
    else { Write-Host "      git repaired." -ForegroundColor Green }
  }
}

Write-Host "[5/6] Screener login + building dashboard data..." -ForegroundColor Cyan
& $exe @pre (Join-Path $Root 'scripts\screener_login.py')
& $exe @pre (Join-Path $Root 'scripts\refresh_scanx.py') --pages 8
New-Item -ItemType Directory (Join-Path $Root 'alerts') -Force | Out-Null
foreach ($of in @('orders.json','orders_companies.json','buybacks.json','special.json')) { $op = Join-Path $Root "docs\data\$of"; if (-not (Test-Path $op)) { Set-Content -Path $op -Value '[]' } }
foreach ($mf in @('orders_meta.json','buybacks_meta.json','special_meta.json')) { $mp = Join-Path $Root "docs\data\$mf"; if (-not (Test-Path $mp)) { Set-Content -Path $mp -Value '{}' } }

$pub = Start-Job -ScriptBlock {
  $e = $using:exe; $p = $using:pre; $r = $using:Root; $nopush = $using:NoPush
  Set-Location $r
  while ($true) {
    & $e @p (Join-Path $r 'scripts\refresh_scanx.py') --pages 8 *> (Join-Path $r 'alerts\refresh.log')
    & $e @p (Join-Path $r 'scripts\refresh_quotes.py') *> (Join-Path $r 'alerts\quotes.log')
    if (-not $nopush) {
      git add -A 2>$null; git diff --cached --quiet 2>$null
      if ($LASTEXITCODE -ne 0) {
        git commit -m "data: $(Get-Date -Format o)" 2>$null | Out-Null
        git push 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {     # cloud quotes workflow pushed meanwhile
          git fetch origin main 2>$null
          git rebase -X theirs origin/main 2>$null
          if ($LASTEXITCODE -ne 0) { git rebase --abort 2>$null }
          git push 2>$null | Out-Null
        }
      }
    }
    Start-Sleep -Seconds 60
  }
}

$ord = Start-Job -ScriptBlock {
  $e = $using:exe; $p = $using:pre; $r = $using:Root
  Set-Location $r; $lastDay = ""; $first = $true
  while ($true) {
    $now = Get-Date; $today = $now.ToString('yyyy-MM-dd')
    $atOpen = ($now.Hour -gt 9) -or ($now.Hour -eq 9 -and $now.Minute -ge 15)
    if ($first -or ($lastDay -ne $today -and $atOpen)) {
      & $e @p (Join-Path $r 'scripts\refresh_orders.py') --months 3 *> (Join-Path $r 'alerts\orders.log')
      & $e @p (Join-Path $r 'scripts\refresh_buybacks.py') --months 12 *> (Join-Path $r 'alerts\buybacks.log')
      & $e @p (Join-Path $r 'scripts\refresh_special.py') *> (Join-Path $r 'alerts\special.log')
      & $e @p (Join-Path $r 'scripts\refresh_fii.py') *> (Join-Path $r 'alerts\fii.log')
      & $e @p (Join-Path $r 'scripts\refresh_marketpulse.py') *> (Join-Path $r 'alerts\marketpulse.log')
      & $e @p (Join-Path $r 'scripts\refresh_fpi.py') *> (Join-Path $r 'alerts\fpi.log')
      & $e @p (Join-Path $r 'scripts\refresh_sectors.py') *> (Join-Path $r 'alerts\sectors.log')
      & $e @p (Join-Path $r 'scripts\refresh_technofunda.py') --mcap-floor 5 --screen-pages 250 *> (Join-Path $r 'alerts\technofunda.log')
      & $e @p (Join-Path $r 'scripts\refresh_magicformula.py') --screen-pages 250 *> (Join-Path $r 'alerts\magicformula.log')
      & $e @p (Join-Path $r 'scripts\refresh_fundamentals.py') --top 150 --skip-existing *> (Join-Path $r 'alerts\fundamentals.log')
      & $e @p (Join-Path $r 'scripts\refresh_iv.py') --per-sector 30 *> (Join-Path $r 'alerts\iv.log')
      $lastDay = $today; $first = $false
    }
    Start-Sleep -Seconds 900
  }
}

$rt = $null
if (-not $NoRealtime) {
  $rt = Start-Job -ScriptBlock {
    $e = $using:exe; $p = $using:pre; $r = $using:Root
    Set-Location $r
    & $e @p (Join-Path $r 'scripts\run_realtime.py') *> (Join-Path $r 'alerts\realtime.log')
  }
}

Write-Host "`n[6/6] LIVE. Dashboard: http://localhost:$Port" -ForegroundColor Green
Write-Host "      Data pushes to GitHub every 60s. Keep this window open; Ctrl+C to stop." -ForegroundColor Green
Start-Sleep -Milliseconds 700
Start-Process "http://localhost:$Port/"
try {
  & $exe @pre (Join-Path $Root 'scripts\serve.py') --port $Port
} finally {
  foreach ($j in @($pub, $rt, $ord)) { if ($j) { Stop-Job $j -ErrorAction SilentlyContinue; Remove-Job $j -ErrorAction SilentlyContinue } }
  Write-Host "Stopped scanX LIVE." -ForegroundColor Green
}
