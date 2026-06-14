<#
.SYNOPSIS
    Starts the FPI Dashboard sync helper (fpi_server.py).

.DESCRIPTION
    Launches the local HTTP helper that lets the dashboard's Sync button
    run fpi_update.py on demand. Listens on http://127.0.0.1:8765
    (loopback only — not exposed to the network).

    Keep this PowerShell window open while you want Sync to work.
    Close it (or Ctrl+C) to stop.

.NOTES
    How to run:
      Option A — right-click this file -> "Run with PowerShell"
      Option B — from any PowerShell prompt:
          cd D:\FPI
          .\start_sync_helper.ps1

    If Windows blocks .ps1 execution with a policy error, run this once
    in PowerShell (does NOT need admin):
          Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#>

[CmdletBinding()]
param(
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ScriptDir

$Host.UI.RawUI.WindowTitle = "FPI Sync Helper - keep this window open"

function Write-Banner {
    param([string]$Text, [ConsoleColor]$Color = 'Cyan')
    Write-Host ('=' * 60) -ForegroundColor $Color
    Write-Host (" $Text") -ForegroundColor $Color
    Write-Host ('=' * 60) -ForegroundColor $Color
}

Write-Banner 'FPI Sync Helper'
Write-Host " Folder : $ScriptDir"
Write-Host " Port   : $Port  (http://127.0.0.1:$Port)"
Write-Host ''
Write-Host ' The Sync button in FPI_Dashboard.html will work as long as'
Write-Host ' this window stays open. Close it (or press Ctrl+C) to stop.'
Write-Host ''

# ── Preflight: fpi_server.py present? ────────────────────────────────────
$Server = Join-Path $ScriptDir 'fpi_server.py'
if (-not (Test-Path -LiteralPath $Server)) {
    Write-Host "ERROR: fpi_server.py not found in $ScriptDir" -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}

# ── Preflight: pick a Python interpreter ─────────────────────────────────
function Get-PythonCmd {
    foreach ($candidate in 'python', 'py') {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}
$Python = Get-PythonCmd
if (-not $Python) {
    Write-Host 'ERROR: Neither "python" nor "py" was found on PATH.' -ForegroundColor Red
    Write-Host 'Install Python 3.10+ from https://www.python.org/downloads/' -ForegroundColor Yellow
    Write-Host '(tick "Add Python to PATH" during install), then re-run.'
    Read-Host 'Press Enter to close'
    exit 1
}
Write-Host " Python : $Python"

# ── Preflight: required Python packages ──────────────────────────────────
# fpi_update.py imports requests, bs4 (beautifulsoup4), openpyxl.
# Probe the chosen interpreter; pip-install anything missing.
$RequiredImports = @{
    'requests' = 'requests'
    'bs4'      = 'beautifulsoup4'
    'openpyxl' = 'openpyxl'
}

$probeScript = @'
import importlib, sys
mods = sys.argv[1:]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
print(",".join(missing))
'@

$missingList = & $Python -c $probeScript @($RequiredImports.Keys)
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARN: could not probe Python packages (exit $LASTEXITCODE). Continuing anyway." -ForegroundColor Yellow
    $missingList = ''
}
$missing = @($missingList.Trim().Split(',') | Where-Object { $_ -ne '' })

if ($missing.Count -gt 0) {
    $pipNames = $missing | ForEach-Object { $RequiredImports[$_] }
    Write-Host ''
    Write-Host (" Missing Python packages: {0}" -f ($pipNames -join ', ')) -ForegroundColor Yellow
    Write-Host ' Installing now (one-time setup)...' -ForegroundColor Yellow
    & $Python -m pip install --quiet --disable-pip-version-check @pipNames
    if ($LASTEXITCODE -ne 0) {
        Write-Host ''
        Write-Host "ERROR: pip install failed (exit $LASTEXITCODE)." -ForegroundColor Red
        Write-Host "Try running manually:" -ForegroundColor Yellow
        Write-Host ("  `"$Python`" -m pip install {0}" -f ($pipNames -join ' '))
        Read-Host 'Press Enter to close'
        exit 1
    }
    Write-Host ' Packages installed.' -ForegroundColor Green
}

# ── Preflight: is the port already in use? ───────────────────────────────
$inUse = $false
try {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    $listener.Start()
    $listener.Stop()
} catch {
    $inUse = $true
}

if ($inUse) {
    Write-Host ''
    Write-Host "Port $Port is already in use." -ForegroundColor Yellow
    Write-Host 'Most likely the helper is already running in another window —' -ForegroundColor Yellow
    Write-Host 'try the dashboard Sync button. If it still fails, close any' -ForegroundColor Yellow
    Write-Host 'other PowerShell/CMD windows running this script and retry.' -ForegroundColor Yellow
    Read-Host 'Press Enter to close'
    exit 1
}

Write-Host ''
Write-Host 'Starting helper... (Ctrl+C to stop)' -ForegroundColor Green
Write-Host ''

# ── Run the server in the foreground so its output streams to this window
# Using the call operator (&) keeps stdout/stderr inline and lets Ctrl+C
# propagate to the Python process.
try {
    & $Python $Server
} catch {
    Write-Host ''
    Write-Host "Helper crashed: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    Write-Host ''
    Write-Host 'Helper stopped.' -ForegroundColor Cyan
    Read-Host 'Press Enter to close this window'
}
