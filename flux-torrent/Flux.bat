@echo off
:: Flux Torrent Client - Double-click to launch
:: This wrapper calls the PowerShell installer/launcher with proper execution policy.
title Flux Torrent
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-Flux.ps1" %*
if %ERRORLEVEL% neq 0 (
    echo.
    echo Something went wrong. Check the output above.
    pause
)
