# Build the C / RTI Connext demo participant. Needs RTI Connext Professional 7.x
# (host and target bundles), CMake >= 3.11 and Visual Studio (C compiler). Never
# installs anything. Result: build\Release\rti_c.exe
#
#   $env:NDDSHOME = "C:\Program Files\rti_connext_dds-7.3.0"
#   $env:CONNEXTDDS_ARCH = "x64Win64VS2017"   # only if several archs are installed
#   .\build.ps1
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not $env:NDDSHOME) {
    Write-Error "NDDSHOME is not set (RTI Connext Professional 7.x install directory)"
    exit 1
}

$cmakeArgs = @("-S", ".", "-B", "build", "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=ON")
if ($env:CONNEXTDDS_ARCH) {
    $cmakeArgs += "-DCONNEXTDDS_ARCH=$($env:CONNEXTDDS_ARCH)"
}

& cmake @cmakeArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& cmake --build build --config Release
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "built: $PSScriptRoot\build\Release\rti_c.exe"
