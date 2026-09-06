param([string]$Python = "python", [string]$Database = "")
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Database) { $Database = Join-Path $projectRoot "outputs\sqlite\project_workspace.db" }
$logDir = Join-Path $projectRoot "logs\generation-worker"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:PYTHONUTF8 = "1"
$mutex = New-Object System.Threading.Mutex($false, "Global\TestAgentGenerationWorkerSupervisor")
if (-not $mutex.WaitOne(0)) { exit 0 }
try {
    while ($true) {
        & $Python -X faulthandler (Join-Path $PSScriptRoot "run_generation_worker.py") --db $Database --idle-exit-seconds 86400 *>> (Join-Path $logDir "supervisor-worker.log")
        "$(Get-Date -Format o) worker exited with code $LASTEXITCODE; restarting in 10 seconds" | Add-Content -Encoding UTF8 (Join-Path $logDir "supervisor.log")
        Start-Sleep -Seconds 10
    }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
