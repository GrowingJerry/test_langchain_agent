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
    Write-Host "[OK] Streamlit 已启动，PID=$($streamlit.Id)"
} else { Write-Host "[OK] Streamlit 已在端口 $Port 运行" }

$supervisor = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "run-generation-worker-supervisor.ps1"),"-Python",$Python,"-Database",$Database) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
Write-Host "[OK] generation worker supervisor 已启动，PID=$($supervisor.Id)"

$ready=$false
for($i=0;$i -lt 30;$i++) {
    Start-Sleep -Seconds 1
    try { Invoke-WebRequest -Uri "http://127.0.0.1:$Port" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ready=$true; break } catch {}
}
if (-not $ready) { throw "Streamlit 在30秒内未就绪，请查看 $logDir\streamlit.err.log" }
Write-Host "[READY] 页面：http://127.0.0.1:$Port"
Write-Host "[READY] 后台任务数据库：$Database"
Write-Host "此窗口现在可以关闭；后台服务不会依赖此窗口继续运行。"
