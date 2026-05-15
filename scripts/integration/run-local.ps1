# PowerShell standalone local runner for TopicForge integration scenarios.
#
# Probes locally installed DDS Python bindings, runs each scenario
# that has its required vendors available, and skips the rest with
# a clear message. No Docker required.
#
# Usage:
#   .\scripts\integration\run-local.ps1
#
# Pre-requisites (any one of these gets you partial coverage):
#   pip install "topicforge[dds-cyclone]"
#   pip install "topicforge[dds-fast]"
#   pip install "topicforge[dds-opendds]"  # not yet on PyPI ; placeholder

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
$ScenariosDir = Join-Path $RepoRoot "tests\integration\scenarios"
$Runner = Join-Path $RepoRoot "scripts\integration\scenarios_runner.py"

if (-not (Test-Path $ScenariosDir)) {
    Write-Error "scenarios dir not found at $ScenariosDir"
    exit 2
}
if (-not (Test-Path $Runner)) {
    Write-Error "runner not found at $Runner"
    exit 2
}

Write-Host "[run-local] dispatching scenarios from $ScenariosDir"
& python $Runner --scenarios $ScenariosDir
