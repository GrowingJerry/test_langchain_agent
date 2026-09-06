param([string]$Python = "python", [int]$Port = 8501, [string]$Database = "")
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $projectRoot "logs\generation-worker"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:PYTHONUTF8 = "1"
if (-not $Database) { $Database = Join-Path $projectRoot "outputs\sqlite\project_workspace.db" }

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    $streamlit = Start-Process -FilePath $Python -ArgumentList @("-m","streamlit","run","app.py","--server.headless=true","--server.port=$Port") -WorkingDirectory $projectRoot -RedirectStandardOutput (Join-Path $logDir "streamlit.out.log") -RedirectStandardError (Join-Path $logDir "streamlit.err.log") -WindowStyle Hidden -PassThru
    Write-Host "[OK] Streamlit started. PID=$($streamlit.Id)"
} else { Write-Host "[OK] Streamlit is already listening on port $Port." }

$supervisor = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run-generation-worker-supervisor.ps1"),"-Python",$Python,"-Database",$Database) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
Write-Host "[OK] Generation worker supervisor started. PID=$($supervisor.Id)"

$ready=$false
for($i=0;$i -lt 30;$i++) {
    Start-Sleep -Seconds 1
    try { Invoke-WebRequest -Uri "http://127.0.0.1:$Port" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ready=$true; break } catch {}
}
if (-not $ready) { throw "Streamlit was not ready within 30 seconds. Check $logDir\streamlit.err.log" }
Write-Host "[READY] UI: http://127.0.0.1:$Port"
Write-Host "[READY] Generation database: $Database"
Write-Host "This terminal may now be closed. Background services are independent of it."
