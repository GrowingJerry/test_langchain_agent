param([string]$Python = "python")
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $projectRoot "logs\xiaoche"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:PYTHONUTF8 = "1"
Push-Location $projectRoot
try {
    & $Python -m desktop_pet.app *>> (Join-Path $logDir "desktop.log")
    exit $LASTEXITCODE
} finally { Pop-Location }
