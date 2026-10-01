# Prepare the TopicForge multi-vendor demo on Windows.
# Creates .venv-demo at the repo root, installs TopicForge with the Cyclone
# binding, and builds the Rust / Dust participant if cargo is available.
#
#   -Firewall   also adds inbound Windows Firewall rules for UDP 7400-7500
#               (DDS discovery and data) for the demo programs. Needs an
#               elevated (administrator) PowerShell. Only needed when the
#               participants run on several machines; a single host works
#               without it.
param([switch]$Firewall)
$ErrorActionPreference = "Stop"

$Repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$Venv = Join-Path $Repo ".venv-demo"
$Py = Join-Path $Venv "Scripts\python.exe"

Write-Host "[setup] repo: $Repo"
if (-not (Test-Path $Venv)) {
    # cyclonedds ships wheels for CPython 3.10 to 3.13 only; take the newest of those.
    $ver = @("3.13", "3.12", "3.11", "3.10") | Where-Object { py "-$_" -c "pass" 2>$null; $LASTEXITCODE -eq 0 } |
        Select-Object -First 1
    if (-not $ver) { throw "Python 3.10 to 3.13 is required (cyclonedds has no wheel for newer versions)" }
    py "-$ver" -m venv $Venv
}
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet -e "$Repo[dds]"
& $Py -m pip install --quiet "dust-dds==0.16.0"  # Python / Dust participant
Write-Host "[setup] TopicForge + cyclonedds installed in $Venv"

if (Get-Command cargo -ErrorAction SilentlyContinue) {
    Push-Location (Join-Path $Repo "scripts\integration\publishers\dust_publisher")
    cargo build --release --quiet
    Pop-Location
    Write-Host "[setup] Rust / Dust participant built"
} else {
    Write-Host "[setup] cargo not found: install Rust (https://rustup.rs) to build the Dust participant"
}

if ($Firewall) {
    # The venv python.exe is a launcher that starts the base interpreter, so
    # both need a rule. Built demo programs are found by their known names.
    $demoExes = @("dust_publisher", "fast_publisher", "cyclone_c", "cyclone_cpp",
        "cyclone_rust", "dust_c_publisher", "rti_c", "rti_cpp", "rti_rust", "ospl_publisher")
    $built = Get-ChildItem (Join-Path $Repo "scripts\integration\publishers") -Recurse -Filter *.exe `
        -ErrorAction SilentlyContinue |
        Where-Object { $demoExes -contains $_.BaseName } |
        ForEach-Object { $_.FullName }
    $programs = @($Py, (& $Py -c "import sys; print(sys._base_executable)")) + @($built)
    foreach ($p in ($programs | Select-Object -Unique)) {
        if (Test-Path $p) {
            $name = "TopicForge demo DDS - " + [IO.Path]::GetFileName($p)
            Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
            New-NetFirewallRule -DisplayName $name -Direction Inbound -Program $p `
                -Protocol UDP -LocalPort 7400-7500 -Action Allow -Profile Private | Out-Null
            Write-Host "[setup] firewall rule added: $name"
        }
    }
}

& $Py (Join-Path $Repo "scripts\integration\driver\demo_client.py") --list
Write-Host "[setup] done. Run: scripts\integration\launch\run_demo.ps1"
