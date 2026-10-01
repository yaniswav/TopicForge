# Build the C / Dust DDS demo participant on Windows.
#
# Run from a "Developer PowerShell for VS" (or x64 Native Tools prompt) so
# that cl.exe is on PATH. Needs git and cargo (Rust >= 1.87, edition 2024).
# Output: .\build\dust_c_publisher.exe and .\build\dust_dds_c.dll next to it.
#
# The C binding (dust_dds_c) is not published on crates.io (publish = false),
# so the Dust DDS repository is cloned at a pinned commit under .\build.
$ErrorActionPreference = "Stop"

# Head of s2e-systems/dust-dds main on 2026-10-01 (dust_dds_c 0.17.0).
$DustCommit = "74a70d7101cb1562e01c2469db62b24e7ec19a64"

# Optional: pass a rustup toolchain, e.g. $env:DUST_CARGO_TOOLCHAIN = "1.97.0".
$CargoArgs = @()
if ($env:DUST_CARGO_TOOLCHAIN) { $CargoArgs += "+$($env:DUST_CARGO_TOOLCHAIN)" }

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Build = Join-Path $Here "build"
$Src = Join-Path $Build "dust-dds"
$Gen = Join-Path $Build "gen"

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE" }
}

New-Item -ItemType Directory -Force $Build, $Gen | Out-Null
if (-not (Test-Path (Join-Path $Src ".git"))) {
    Invoke-Checked git @("clone", "https://github.com/s2e-systems/dust-dds.git", $Src)
}
Invoke-Checked git @("-C", $Src, "checkout", "--quiet", $DustCommit)

# dust_dds_c builds the dll/import lib and writes include\dust_dds.h
# (cbindgen); dust_dds_gen turns Heartbeat.idl into Heartbeat.h.
Push-Location $Src
try {
    Invoke-Checked cargo ($CargoArgs + @("build", "--release", "-p", "dust_dds_c", "-p", "dust_dds_gen"))
} finally {
    Pop-Location
}
$Release = Join-Path $Src "target\release"
Invoke-Checked (Join-Path $Release "dust_dds_gen.exe") @((Join-Path $Here "Heartbeat.idl"), (Join-Path $Gen "Heartbeat.h"))

if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    throw "cl.exe not found: run this script from a Developer PowerShell for Visual Studio"
}
Copy-Item (Join-Path $Release "dust_dds_c.dll") $Build -Force
Invoke-Checked cl.exe @(
    "/nologo", "/O2", "/W3", "/TC",
    "/I$(Join-Path $Src 'bindings\c\include')", "/I$Gen",
    (Join-Path $Here "main.c"),
    "/Fo:$Build\", "/Fe:$(Join-Path $Build 'dust_c_publisher.exe')",
    "/link", (Join-Path $Release "dust_dds_c.dll.lib")
)

Write-Host "built $(Join-Path $Build 'dust_c_publisher.exe')"
