param([string]$Python="python")
$ErrorActionPreference="Stop"
$root=(Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:PLAYWRIGHT_BROWSERS_PATH=Join-Path $root "vendor\playwright-browsers"
Set-Location $root
& $Python scripts\check_playwright_runtime.py
& $Python -m pytest tests\unit\test_offline_site_service.py -m playwright -q
