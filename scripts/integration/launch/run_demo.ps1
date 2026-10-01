# Run the TopicForge multi-vendor demo on Windows. Run setup.ps1 first.
# "Continue", not "Stop": under Windows PowerShell 5.1, "Stop" would abort on
# the first line the driver writes to stderr. The exit code carries the result.
$ErrorActionPreference = "Continue"
$Repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$Py = Join-Path $Repo ".venv-demo\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "[run_demo] .venv-demo is missing: run scripts\integration\launch\setup.ps1 first"
    exit 1
}
& $Py (Join-Path $Repo "scripts\integration\driver\demo_client.py") @args
exit $LASTEXITCODE
