param([string]$Python="python")
$ErrorActionPreference="Stop"
$root=(Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$wheelhouse=Join-Path $root "wheelhouse"
$browsers=Join-Path $root "vendor\playwright-browsers"
New-Item -ItemType Directory -Force $wheelhouse,$browsers | Out-Null
& $Python -m pip download --dest $wheelhouse --only-binary=:all: -r (Join-Path $root "requirements-playwright.txt")
$env:PLAYWRIGHT_BROWSERS_PATH=$browsers
& $Python -m playwright install chromium
$files=Get-ChildItem $wheelhouse,$browsers -Recurse -File | ForEach-Object { [pscustomobject]@{Path=$_.FullName.Substring($root.Length+1);Length=$_.Length;SHA256=(Get-FileHash $_.FullName -Algorithm SHA256).Hash} }
$files | ConvertTo-Json -Depth 3 | Set-Content (Join-Path $root "playwright-runtime-manifest.json") -Encoding UTF8
$size=($files | Measure-Object Length -Sum).Sum
Write-Host "Playwright runtime ready: $([math]::Round($size/1MB,2)) MB"
& $Python (Join-Path $root "scripts\check_playwright_runtime.py")
