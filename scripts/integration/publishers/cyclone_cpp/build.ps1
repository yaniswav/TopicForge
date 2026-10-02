# Configure and build with CMake. The Cyclone DDS install prefix comes from
# CYCLONEDDS_HOME and/or CMAKE_PREFIX_PATH (';' separated).
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$parts = @($env:CYCLONEDDS_HOME, $env:CMAKE_PREFIX_PATH) | Where-Object { $_ }
if (-not $parts) {
    Write-Error 'set CYCLONEDDS_HOME (or CMAKE_PREFIX_PATH) to the Cyclone DDS install prefix'
}
$prefix = $parts -join ';'

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release "-DCMAKE_PREFIX_PATH=$prefix"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cmake --build build --config Release
exit $LASTEXITCODE
