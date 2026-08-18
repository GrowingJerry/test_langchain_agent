param([string]$Python="python")
$ErrorActionPreference="Stop"
$root=(Resolve-Path (Join-Path $PSScriptRoot "..")).Path
& $Python -m pip install --no-index --find-links (Join-Path $root "wheelhouse") -r (Join-Path $root "requirements-playwright.txt")
$env:PLAYWRIGHT_BROWSERS_PATH=Join-Path $root "vendor\playwright-browsers"
& $Python (Join-Path $root "scripts\check_playwright_runtime.py")
