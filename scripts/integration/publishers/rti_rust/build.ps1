# Build the Rust / RTI Connector demo participant. Needs Rust 1.85+ and network
# access: cargo fetches the rtiddsconnector git dependency, and its build.rs
# downloads the native Connector libraries (connectorlibs-1.5.0.zip) from
# github.com/rticommunity/rticonnextdds-connector. No RTI Connext install is
# needed to build. Result: target\release\rti_rust.exe, with the native
# libraries (DLLs) copied next to it.
#
# To build offline from a local copy of the native libraries, set
# $env:RTI_CONNECTOR_DIR to a directory containing lib\<arch>\ (see the crate's
# docs/guide/getting_started.md).
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

& cargo build --release
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# build.rs extracts the native libraries to <OUT_DIR>\lib\<arch>; copy them next
# to the executable (documented as one way to make them discoverable).
$libDir = Get-ChildItem -Path "target\release\build\rtiddsconnector-*\out\lib\*" -Directory -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $libDir) {
    Write-Error "native Connector libraries not found under target\release\build"
    exit 1
}
Get-ChildItem -LiteralPath $libDir.FullName -File | Copy-Item -Destination "target\release" -Force

Write-Host "built: $PSScriptRoot\target\release\rti_rust.exe"
