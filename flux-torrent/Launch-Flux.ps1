#Requires -Version 5.1
<#
.SYNOPSIS
    Flux Torrent Client - Installer and Launcher
.NOTES
    Zero python -c commands. All Python logic lives in .py files.
    fix_libtorrent.py handles all DLL detection/download/fixing.
#>

$ErrorActionPreference = 'Continue'
$Host.UI.RawUI.WindowTitle = "Flux Torrent - Launcher"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvDir    = Join-Path $ScriptDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

# ── Helpers ─────────────────────────────────────────────────────────────────
function Write-Header { param([string]$T); Write-Host ""; Write-Host "  $T" -ForegroundColor Cyan; Write-Host "  $('-' * $T.Length)" -ForegroundColor DarkCyan }
function Write-Step  { param([string]$T); Write-Host "  [*] $T" -ForegroundColor DarkGray }
function Write-OK    { param([string]$T); Write-Host "  [+] $T" -ForegroundColor Green }
function Write-Warn  { param([string]$T); Write-Host "  [!] $T" -ForegroundColor Yellow }
function Write-Err   { param([string]$T); Write-Host "  [X] $T" -ForegroundColor Red }
function Wait-Exit   { Write-Host ""; Write-Host "  Press Enter to exit..." -ForegroundColor DarkGray; $null = Read-Host; exit 1 }

function Test-PythonExe {
    param([string]$P)
    if (-not $P -or -not (Test-Path $P -EA SilentlyContinue)) { return $null }
    try {
        $tmp = Join-Path $env:TEMP "flux_pyver_$PID.txt"
        Start-Process -FilePath $P -ArgumentList "--version" -NoNewWindow -Wait `
            -RedirectStandardOutput $tmp -RedirectStandardError "$tmp.err" -EA Stop | Out-Null
        $text = ""
        if (Test-Path $tmp) { $text += Get-Content $tmp -Raw -EA SilentlyContinue }
        if (Test-Path "$tmp.err") { $text += Get-Content "$tmp.err" -Raw -EA SilentlyContinue }
        Remove-Item $tmp, "$tmp.err" -Force -EA SilentlyContinue
        if ($text -match 'Python (\d+)\.(\d+)\.(\d+)') {
            if ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 9) {
                return "$($Matches[1]).$($Matches[2]).$($Matches[3])"
            }
        }
    } catch {}
    return $null
}

# ── Banner ──────────────────────────────────────────────────────────────────
Clear-Host
Write-Host ""
Write-Host "   ______  __    __  __  __  __  __" -ForegroundColor Blue
Write-Host "  /\  ___\/\ \  /\ \/\ \/\ \/\ \/\ \" -ForegroundColor Blue
Write-Host "  \ \  __\\ \ \__\ \ \_\ \_\ \_\ \ \" -ForegroundColor Blue
Write-Host "   \ \_\   \ \_____\ \_____/\_____\ \_\" -ForegroundColor Blue
Write-Host "    \/_/    \/_____/\/_____/\/_____/\/_/" -ForegroundColor DarkBlue
Write-Host ""
Write-Host "   Flux Torrent Client v1.0" -ForegroundColor DarkCyan
Write-Host "   Premium. Debloated. Fast." -ForegroundColor DarkGray
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
#  STEP 1: Find Python 3.9+
# ══════════════════════════════════════════════════════════════════════════════
Write-Header "Checking Python"
$Found = $null

# Existing venv
if (Test-Path $VenvPython) {
    $v = Test-PythonExe $VenvPython
    if ($v) { $Found = $VenvPython; Write-OK "Existing venv: Python $v" }
}

# PATH
if (-not $Found) {
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -EA SilentlyContinue
        if ($cmd -and $cmd.Source) {
            $v = Test-PythonExe $cmd.Source
            if ($v) { $Found = $cmd.Source; Write-OK "Found in PATH: Python $v"; break }
        }
    }
}

# py launcher
if (-not $Found) {
    $pyExe = Get-Command "py" -EA SilentlyContinue
    if ($pyExe) {
        $tmpPy = Join-Path $env:TEMP "flux_getexe_$PID.py"
        Set-Content -Path $tmpPy -Value "import sys; print(sys.executable)" -Encoding UTF8
        try {
            $resolved = (& py -3 $tmpPy 2>&1 | Out-String).Trim()
            if ($resolved -and (Test-Path $resolved -EA SilentlyContinue)) {
                $v = Test-PythonExe $resolved
                if ($v) { $Found = $resolved; Write-OK "Found via py launcher: Python $v" }
            }
        } catch {}
        Remove-Item $tmpPy -Force -EA SilentlyContinue
    }
}

# Registry
if (-not $Found) {
    foreach ($root in @("HKCU:\SOFTWARE\Python\PythonCore", "HKLM:\SOFTWARE\Python\PythonCore")) {
        if (-not (Test-Path $root -EA SilentlyContinue)) { continue }
        Get-ChildItem $root -EA SilentlyContinue | Sort-Object Name -Descending | ForEach-Object {
            if ($script:Found) { return }
            $ipKey = Join-Path $_.PSPath "InstallPath"
            if (-not (Test-Path $ipKey -EA SilentlyContinue)) { return }
            $props = Get-ItemProperty $ipKey -EA SilentlyContinue
            $exe = $props.ExecutablePath
            if (-not $exe) {
                $dir = $props.'(default)'
                if ($dir) { $exe = Join-Path $dir.TrimEnd('\') "python.exe" }
            }
            if ($exe -and (Test-Path $exe -EA SilentlyContinue)) {
                $v = Test-PythonExe $exe
                if ($v) { $script:Found = $exe; Write-OK "Found via Registry: Python $v" }
            }
        }
    }
}

# Common directories
if (-not $Found) {
    Write-Step "Scanning directories..."
    foreach ($pv in @('313','312','311','310','39')) {
        foreach ($base in @("$env:LOCALAPPDATA\Programs\Python\Python$pv", "C:\Python$pv", "$env:ProgramFiles\Python$pv")) {
            $p = "$base\python.exe"
            if (Test-Path $p -EA SilentlyContinue) {
                $v = Test-PythonExe $p
                if ($v) { $Found = $p; Write-OK "Found: Python $v"; break }
            }
        }
        if ($Found) { break }
    }
}

if (-not $Found) {
    Write-Err "Python 3.9+ not found."
    Write-Host "  Install from: https://www.python.org/downloads/" -ForegroundColor Cyan
    Wait-Exit
}

# ══════════════════════════════════════════════════════════════════════════════
#  STEP 2: Virtual Environment
# ══════════════════════════════════════════════════════════════════════════════
if ($Found -ne $VenvPython) {
    if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir -EA SilentlyContinue }
    Write-Header "Creating Virtual Environment"
    Write-Step "Using: $Found"
    & $Found -m venv $VenvDir 2>&1 | Out-Null
    if (-not (Test-Path $VenvPython)) { Write-Err "venv creation failed"; Wait-Exit }
    Write-OK "Created .venv\"
}

$Py = $VenvPython
& $Py -m pip install --upgrade pip --quiet 2>&1 | Out-Null

# ══════════════════════════════════════════════════════════════════════════════
#  STEP 3: Install pip packages
# ══════════════════════════════════════════════════════════════════════════════
Write-Header "Installing Dependencies"
Write-Step "Installing PyQt6 and libtorrent (if needed)..."
& $Py -m pip install "PyQt6>=6.6.0" "libtorrent>=2.0.0" --quiet 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Err "pip install failed"
    Wait-Exit
}
Write-OK "PyQt6 and libtorrent packages ready"

# ══════════════════════════════════════════════════════════════════════════════
#  STEP 4: Fix libtorrent DLLs (the fix script handles EVERYTHING)
# ══════════════════════════════════════════════════════════════════════════════
Write-Header "Fixing libtorrent DLLs"
Write-Step "Running fix_libtorrent.py (auto-detects and fixes all DLL issues)..."
Write-Host ""

$env:PYTHONPATH = $ScriptDir
$fixScript = Join-Path $ScriptDir "fix_libtorrent.py"
& $Py $fixScript
$fixResult = $LASTEXITCODE

if ($fixResult -ne 0) {
    Write-Host ""
    Write-Err "DLL fix failed. See output above for details."
    Wait-Exit
}

# ══════════════════════════════════════════════════════════════════════════════
#  STEP 5: Launch
# ══════════════════════════════════════════════════════════════════════════════
Write-Header "Launching Flux Torrent"
Write-Host ""; Write-Host "  Starting..." -ForegroundColor Green; Write-Host ""

Push-Location $ScriptDir
$env:PYTHONPATH = $ScriptDir
try {
    & $Py (Join-Path $ScriptDir "_bootstrap.py") @args
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Flux exited with code $LASTEXITCODE"
        $log = Join-Path $env:USERPROFILE ".flux-torrent\logs\flux.log"
        if (Test-Path $log) {
            Get-Content $log -Tail 10 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        }
        Wait-Exit
    }
} finally { Pop-Location }
