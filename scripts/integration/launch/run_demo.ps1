# Run the TopicForge multi-vendor demo on Windows. Run setup.ps1 first.
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
& (Join-Path $Repo ".venv-demo\Scripts\python.exe") (Join-Path $Repo "scripts\integration\driver\demo_client.py") @args
exit $LASTEXITCODE
