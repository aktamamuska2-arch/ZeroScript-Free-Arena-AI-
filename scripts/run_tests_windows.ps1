# SPDX-License-Identifier: GPL-3.0-or-later
# run_tests_windows.ps1 - run the ZeroScript test suites on Windows.
# (The sandbox has no Windows access, so THIS FILE IS UNTESTED on Windows -
#  it is the documented way to run the suite there; see the report.)
#
# Usage (PowerShell, from the repo root):
#   powershell -ExecutionPolicy Bypass -File scripts\run_tests_windows.ps1
#
# What it does:
#   1. checks python + node are available;
#   2. installs the `websockets` package for the e2e layer if missing
#      (into the user's environment - add -NoInstall to skip e2e);
#   3. runs test_bridge_addons.py (unit + e2e) and the extension JS suites;
#   4. exits non-zero if anything failed.
param(
    [switch]$NoInstall
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
Write-Host "== ZeroScript test runner (Windows) ==" -ForegroundColor Cyan
Write-Host "repo: $repo"

# ── python ────────────────────────────────────────────────────────────────
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Host "FAIL: python not found on PATH - install Python 3.10+ from python.org" -ForegroundColor Red
    exit 1
}
$pyCmd = $py.Source
Write-Host "python: $pyCmd"
& $pyCmd --version

# ── websockets (needed by the e2e layer) ──────────────────────────────────
if (-not $NoInstall) {
    $missing = & $pyCmd -c "import websockets" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "installing websockets (user site)..."
        & $pyCmd -m pip install --user websockets
        if ($LASTEXITCODE -ne 0) {
            Write-Host "FAIL: could not install websockets - rerun with -NoInstall to skip e2e" -ForegroundColor Red
            exit 1
        }
    }
}

$failed = $false

# ── bridge suite (unit + e2e; e2e auto-skips without websockets) ──────────
Write-Host "`n== test_bridge_addons.py (unit + e2e) ==" -ForegroundColor Cyan
& $pyCmd test_bridge_addons.py
if ($LASTEXITCODE -ne 0) { $failed = $true }

# ── extension JS suites (need node; skip cleanly if absent) ───────────────
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    Push-Location (Join-Path $repo "zeroscript-extension")
    foreach ($t in @("test-parser.js", "test-chatgpt.js")) {
        Write-Host "`n== $t ==" -ForegroundColor Cyan
        & node $t
        if ($LASTEXITCODE -ne 0) { $failed = $true }
    }
    Pop-Location
} else {
    Write-Host "node not found - skipping the extension JS suites" -ForegroundColor Yellow
}

Write-Host ""
if ($failed) {
    Write-Host "RESULT: FAILED (see above)" -ForegroundColor Red
    exit 1
}
Write-Host "RESULT: ALL GREEN" -ForegroundColor Green
