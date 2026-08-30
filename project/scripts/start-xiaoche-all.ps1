param([string]$Python = "python", [int]$Port = 8501)
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $projectRoot "logs\xiaoche"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:PYTHONUTF8 = "1"
$ownedStreamlit = $null
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    $ownedStreamlit = Start-Process -FilePath $Python -ArgumentList @("-m","streamlit","run","app.py","--server.headless=true","--server.port=$Port") -WorkingDirectory $projectRoot -RedirectStandardOutput (Join-Path $logDir "streamlit.out.log") -RedirectStandardError (Join-Path $logDir "streamlit.err.log") -PassThru -WindowStyle Hidden
}
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 | Out-Null
} catch {
    "$(Get-Date -Format o) Ollama unavailable: $($_.Exception.Message)" | Add-Content -Encoding UTF8 (Join-Path $logDir "startup.log")
}
Push-Location $projectRoot
try { & $Python -m desktop_pet.app *>> (Join-Path $logDir "desktop.log") }
finally {
    Pop-Location
    if ($ownedStreamlit -and -not $ownedStreamlit.HasExited) { Stop-Process -Id $ownedStreamlit.Id }
}
