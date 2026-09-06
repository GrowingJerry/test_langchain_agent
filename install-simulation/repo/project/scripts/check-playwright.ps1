param([string]$Python="python")
$root=(Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:PLAYWRIGHT_BROWSERS_PATH=Join-Path $root "vendor\playwright-browsers"
& $Python (Join-Path $root "scripts\check_playwright_runtime.py")
exit $LASTEXITCODE
