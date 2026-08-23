param([string]$Python = "python")
$root = Split-Path -Parent $PSScriptRoot
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root "vendor\playwright-browsers"
& $Python -m pytest (Join-Path $root "tests\unit\test_offline_site_service.py") -m playwright -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m compileall -q $root
exit $LASTEXITCODE
