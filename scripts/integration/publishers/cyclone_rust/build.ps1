# Build with cargo against an installed Cyclone DDS (CYCLONEDDS_HOME), or pass
# --features vendored to build Cyclone DDS from the crate's bundled sources
# (needs cmake). Either way bindgen needs libclang (set LIBCLANG_PATH if it is
# not found automatically).
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not ($args -contains 'vendored') -and -not $env:CYCLONEDDS_HOME) {
    Write-Warning 'CYCLONEDDS_HOME is not set; relying on system-wide Cyclone DDS headers/libs'
}

cargo build --release @args
exit $LASTEXITCODE
