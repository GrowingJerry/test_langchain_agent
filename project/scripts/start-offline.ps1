param([string]$Python="python")
$root=(Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:PLAYWRIGHT_BROWSERS_PATH=Join-Path $root "vendor\playwright-browsers"
$env:OLLAMA_BASE_URL="http://127.0.0.1:11434"
Set-Location $root
& $Python -m streamlit run app.py
