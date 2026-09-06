$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $projectRoot "logs\generation-worker"
foreach ($name in @("supervisor","streamlit")) {
    $pidFile=Join-Path $logDir "$name.pid"
    if (Test-Path -LiteralPath $pidFile) {
        $ownedPid=[int](Get-Content -LiteralPath $pidFile -Raw)
        if (Get-Process -Id $ownedPid -ErrorAction SilentlyContinue) {
            & taskkill.exe /PID $ownedPid /T /F | Out-Null
            if ($LASTEXITCODE -eq 0) { Write-Host "[OK] Stopped $name process tree. PID=$ownedPid" }
            else { Write-Warning "Could not stop $name PID=$ownedPid. Run this script as the same Windows user that started it." }
        }
        Remove-Item -LiteralPath $pidFile -Force
    }
}
