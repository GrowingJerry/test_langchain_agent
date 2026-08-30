param([string]$Python = "python", [int]$Port = 8501)
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $projectRoot
try {
    & $Python -c "from config.settings import settings; from application.container import ApplicationContainer; import desktop_pet.app; print('Python imports: OK'); print('App URL:', settings.xiaoche_app_url)"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $portOpen = [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    Write-Host "Streamlit port ${Port}: $portOpen"
    try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 | Out-Null; Write-Host "Ollama: available" } catch { Write-Host "Ollama: unavailable (chat will show fallback)" }
} finally { Pop-Location }
