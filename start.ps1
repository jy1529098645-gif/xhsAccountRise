#!/usr/bin/env pwsh
# One-click launcher for Windows: starts the FastAPI backend and opens the
# frontend in the default browser.
#
# Usage:  Right-click this file → Run with PowerShell
# Or:     pwsh .\start.ps1

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$venv = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $venv)) {
    Write-Host "Creating venv..." -ForegroundColor Cyan
    & py -3 -m venv .venv
    Write-Host "Installing deps..." -ForegroundColor Cyan
    & $venv -m pip install --upgrade pip
    & $venv -m pip install -r requirements.txt
}

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env -ErrorAction SilentlyContinue
    Write-Host "Created .env from template. Fill in API keys before running again." -ForegroundColor Yellow
    Write-Host "Open .env and add: ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY" -ForegroundColor Yellow
    notepad .env
    exit 1
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = $here

Write-Host "Running migrations..." -ForegroundColor Cyan
& $venv -m studio migrate

Write-Host "Opening frontend in 2s..." -ForegroundColor Cyan
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 3
    Start-Process "https://jy1529098645-gif.github.io/xhsAccountRise/"
} | Out-Null

Write-Host "Starting backend on http://127.0.0.1:8765 ..." -ForegroundColor Green
Write-Host "(Ctrl+C to stop)" -ForegroundColor DarkGray
& $venv -m studio serve --port 8765
