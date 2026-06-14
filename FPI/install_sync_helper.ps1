<#
.SYNOPSIS
    One-time installer for the FPI Dashboard sync helper.

.DESCRIPTION
    Run this script ONCE. After that:
      * The helper auto-starts silently every time you log into Windows.
      * The dashboard's Sync button just works — no terminals to open.
      * No black windows, no manual steps, no remembering anything.

    Specifically, this script:
      1. Locates Python (or installs nothing if it's missing — prompts you).
      2. Pip-installs the required packages (openpyxl, requests, beautifulsoup4).
      3. Stops any helper already running on port 8765.
      4. Creates / replaces a Windows Scheduled Task ("FPI_Sync_Helper")
         that launches fpi_server.py via pythonw.exe (no console window)
         at every user logon, restarting on failure.
      5. Starts the task immediately so you don't have to log out / in.
      6. Verifies the helper is responding on http://127.0.0.1:8765.

    No admin rights are required — the task is created in the current
    user's scope.

.PARAMETER Uninstall
    Removes the scheduled task and stops the running helper.

.NOTES
    Run from PowerShell:
        cd D:\FPI
        .\install_sync_helper.ps1

    Or double-click  install_sync_helper.bat  (which calls this script
    with the right execution-policy bypass).
#>

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$TaskName  = 'FPI_Sync_Helper'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ScriptDir
$Host.UI.RawUI.WindowTitle = 'FPI Sync Helper - Installer'

function Banner($text, $color = 'Cyan') {
    Write-Host ('=' * 60) -ForegroundColor $color
    Write-Host (" $text")  -ForegroundColor $color
    Write-Host ('=' * 60) -ForegroundColor $color
}

function Stop-PortListeners($p) {
    $conns = $null
    try { $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue } catch {}
    if (-not $conns) { return $false }
    $killed = $false
    foreach ($c in $conns) {
        try {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
            $killed = $true
        } catch {}
    }
    if ($killed) { Start-Sleep -Milliseconds 800 }
    return $killed
}

# ── UNINSTALL PATH ──────────────────────────────────────────────────────
if ($Uninstall) {
    Banner 'FPI Sync Helper - Uninstall' 'Yellow'
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host " Removed scheduled task '$TaskName'." -ForegroundColor Green
    } else {
        Write-Host " No scheduled task '$TaskName' found." -ForegroundColor Yellow
    }
    if (Stop-PortListeners $Port) {
        Write-Host " Stopped running helper on port $Port." -ForegroundColor Green
    }
    Write-Host ''
    Read-Host 'Done. Press Enter to close'
    exit 0
}

# ── INSTALL PATH ────────────────────────────────────────────────────────
Banner 'FPI Sync Helper - One-Time Installer'
Write-Host " Folder : $ScriptDir"
Write-Host " Port   : $Port"
Write-Host ''

# 1. Required files
$Server  = Join-Path $ScriptDir 'fpi_server.py'
$Updater = Join-Path $ScriptDir 'fpi_update.py'
foreach ($f in @($Server, $Updater)) {
    if (-not (Test-Path -LiteralPath $f)) {
        Write-Host "ERROR: required file not found: $f" -ForegroundColor Red
        Read-Host 'Press Enter to close'; exit 1
    }
}

# 2. Locate Python (console) — needed for pip install
function Get-Python {
    foreach ($n in 'python','py') {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
    }
    return $null
}
$Python = Get-Python
if (-not $Python) {
    Write-Host 'ERROR: No "python" or "py" on PATH.' -ForegroundColor Red
    Write-Host 'Install Python 3.10+ from https://www.python.org/downloads/' -ForegroundColor Yellow
    Write-Host '(tick "Add Python to PATH" during install), then re-run.'
    Read-Host 'Press Enter to close'; exit 1
}
Write-Host " python : $Python"

# 3. Find pythonw.exe (windowless variant) — preferred for the background task
function Resolve-Pythonw($pythonPath) {
    if ($pythonPath -match 'python(\d*)\.exe$') {
        $candidate = $pythonPath -replace 'python(\d*)\.exe$','pythonw$1.exe'
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    # If user gave us 'py.exe', resolve actual interpreter
    if ($pythonPath -match '\\py\.exe$') {
        try {
            $real = & $pythonPath '-c' 'import sys; print(sys.executable)'
            if ($real) {
                $cand = $real -replace 'python\.exe$','pythonw.exe'
                if (Test-Path -LiteralPath $cand) { return $cand }
            }
        } catch {}
    }
    return $null
}
$Pythonw = Resolve-Pythonw $Python
if ($Pythonw) {
    Write-Host " pythonw: $Pythonw  (silent background)"
} else {
    Write-Host " pythonw: not found — falling back to $Python (a console window may flash on logon)" -ForegroundColor Yellow
    $Pythonw = $Python
}

# 4. Install Python dependencies (idempotent)
Write-Host ''
Write-Host ' [1/5] Ensuring Python packages: openpyxl, requests, beautifulsoup4 ...'
& $Python -m pip install --quiet --disable-pip-version-check openpyxl requests beautifulsoup4
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install failed (exit $LASTEXITCODE)." -ForegroundColor Red
    Write-Host "Try manually:" -ForegroundColor Yellow
    Write-Host "  `"$Python`" -m pip install openpyxl requests beautifulsoup4"
    Read-Host 'Press Enter to close'; exit 1
}
Write-Host '        OK' -ForegroundColor Green

# 5. Stop any existing helper on the target port
Write-Host " [2/5] Freeing port $Port (stopping any current helper) ..."
$stopped = Stop-PortListeners $Port
Write-Host ('        {0}' -f ($(if ($stopped) {'Stopped previous helper.'} else {'(nothing to stop)'}))) -ForegroundColor Green

# 6. Remove any existing scheduled task with this name
Write-Host " [3/5] Registering scheduled task '$TaskName' ..."
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# 7. Create the scheduled task: at logon, hidden, restart on failure
$action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument ('"' + $Server + '"') `
    -WorkingDirectory $ScriptDir

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# ExecutionTimeLimit = 0 means "no limit" (long-running daemon)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew `
    -Hidden

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Runs the FPI Dashboard sync helper (fpi_server.py) silently in the background.' | Out-Null

Write-Host '        OK' -ForegroundColor Green

# 8. Start it now
Write-Host ' [4/5] Starting helper now ...'
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 2

# 9. Verify with up to 6 polls
Write-Host " [5/5] Verifying http://127.0.0.1:$Port/status ..."
$ok = $false
for ($i = 0; $i -lt 6; $i++) {
    try {
        $r = Invoke-WebRequest -Uri ("http://127.0.0.1:$Port/status") -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}

Write-Host ''
if ($ok) {
    Banner 'Installation complete.' 'Green'
    Write-Host ''
    Write-Host '  The helper is running silently in the background.' -ForegroundColor Green
    Write-Host '  It will auto-start every time you log into Windows.' -ForegroundColor Green
    Write-Host '  Just click the Sync button in FPI_Dashboard.html.' -ForegroundColor Green
    Write-Host ''
    Write-Host '  To remove later:'
    Write-Host '      .\install_sync_helper.ps1 -Uninstall'
    Write-Host ''
} else {
    Banner 'Helper not responding yet' 'Yellow'
    Write-Host ''
    Write-Host '  The scheduled task was created but the helper did not'  -ForegroundColor Yellow
    Write-Host "  respond on port $Port within a few seconds."             -ForegroundColor Yellow
    Write-Host "  Check Task Scheduler -> `"$TaskName`" -> `"Last Run Result`"."  -ForegroundColor Yellow
    Write-Host '  Logging out and back in often resolves it.'             -ForegroundColor Yellow
    Write-Host ''
}
Read-Host 'Press Enter to close'
