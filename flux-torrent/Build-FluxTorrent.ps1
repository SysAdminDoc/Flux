<#
.SYNOPSIS
    Build script for Flux Torrent Client - creates a distributable package.

.DESCRIPTION
    Installs dependencies, runs tests, and builds with PyInstaller.
    Output: dist/FluxTorrent/FluxTorrent.exe

.EXAMPLE
    .\Build-FluxTorrent.ps1
    .\Build-FluxTorrent.ps1 -SkipTests
#>

param(
    [switch]$SkipTests,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot

Write-Host "=== Flux Torrent Build Script ===" -ForegroundColor Cyan
Write-Host ""

# Clean previous builds
if ($Clean -or (Test-Path "$ProjectRoot\dist")) {
    Write-Host "[1/5] Cleaning previous builds..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force "$ProjectRoot\dist" -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force "$ProjectRoot\build" -ErrorAction SilentlyContinue
    Remove-Item -Force "$ProjectRoot\*.spec.bak" -ErrorAction SilentlyContinue
}

# Check Python
Write-Host "[2/5] Checking Python environment..." -ForegroundColor Yellow
try {
    $pyVersion = python --version 2>&1
    Write-Host "  Found: $pyVersion" -ForegroundColor Gray
} catch {
    Write-Host "  ERROR: Python not found in PATH" -ForegroundColor Red
    exit 1
}

# Install dependencies
Write-Host "[3/5] Installing dependencies..." -ForegroundColor Yellow
pip install --quiet --upgrade pip
pip install --quiet -r "$ProjectRoot\requirements.txt"
pip install --quiet pyinstaller pytest

# Run tests
if (-not $SkipTests) {
    Write-Host "[4/5] Running tests..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    $testResult = python -m pytest tests/ -v --tb=short 2>&1
    $testExitCode = $LASTEXITCODE
    Pop-Location

    if ($testExitCode -ne 0) {
        Write-Host "  TESTS FAILED:" -ForegroundColor Red
        Write-Host $testResult
        Write-Host ""
        Write-Host "Fix test failures before building." -ForegroundColor Red
        exit 1
    }
    Write-Host "  All tests passed" -ForegroundColor Green
} else {
    Write-Host "[4/5] Skipping tests (--SkipTests)" -ForegroundColor Gray
}

# Build with PyInstaller
Write-Host "[5/5] Building with PyInstaller..." -ForegroundColor Yellow
Push-Location $ProjectRoot
$buildOutput = & pyinstaller flux-torrent.spec --noconfirm 2>&1
$buildExitCode = $LASTEXITCODE
foreach ($line in $buildOutput) {
    $text = "$line"
    if ($text -match "DEPRECATION.*admin") { continue }
    if ($text -match "ERROR") {
        Write-Host "  $text" -ForegroundColor Red
    } elseif ($text -match "WARN") {
        Write-Host "  $text" -ForegroundColor Yellow
    }
}
Pop-Location

if ($buildExitCode -ne 0) {
    Write-Host "  Build failed!" -ForegroundColor Red
    exit 1
}

# Verify output
$exePath = "$ProjectRoot\dist\FluxTorrent\FluxTorrent.exe"
if (Test-Path $exePath) {
    $size = (Get-Item $exePath).Length / 1MB
    Write-Host ""
    Write-Host "=== Build Complete ===" -ForegroundColor Green
    Write-Host "  Output: $exePath" -ForegroundColor Gray
    Write-Host "  Size:   $([math]::Round($size, 1)) MB" -ForegroundColor Gray
    Write-Host ""

    # Create portable zip
    $zipPath = "$ProjectRoot\dist\FluxTorrent-portable.zip"
    Write-Host "Creating portable archive..." -ForegroundColor Yellow
    Compress-Archive -Path "$ProjectRoot\dist\FluxTorrent\*" -DestinationPath $zipPath -Force
    $zipSize = (Get-Item $zipPath).Length / 1MB
    Write-Host "  Archive: $zipPath ($([math]::Round($zipSize, 1)) MB)" -ForegroundColor Gray
} else {
    Write-Host "  ERROR: Output executable not found!" -ForegroundColor Red
    exit 1
}
