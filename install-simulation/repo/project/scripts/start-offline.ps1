param([string]$Python="python")
$root=(Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir=Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:PLAYWRIGHT_BROWSERS_PATH=Join-Path $root "vendor\playwright-browsers"
$env:OLLAMA_BASE_URL="http://127.0.0.1:11434"
$env:PYTHONFAULTHANDLER="1"
$env:PYTHONUNBUFFERED="1"
Set-Location $root
& $Python -X faulthandler -m streamlit run app.py 2>&1 | Tee-Object -FilePath (Join-Path $logDir "streamlit-console.log") -Append
