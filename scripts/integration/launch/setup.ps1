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

function Invoke-Native([string]$What, [scriptblock]$Command) {
    # Windows PowerShell 5.1 turns any stderr line of a native program into a
    # terminating error under "Stop" (pip and cargo print warnings there), so
    # native calls run under "Continue" and are judged by their exit code.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "[setup] $What failed (exit code $LASTEXITCODE)" }
}

Write-Host "[setup] repo: $Repo"
if (-not (Test-Path $Py)) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw "[setup] the Python launcher 'py' is missing: install Python 3.12 from python.org"
    }
    # cyclonedds ships wheels for CPython 3.10 to 3.13 only; take the newest of those.
    $installed = (py -0p) -join "`n"
    $ver = @("3.13", "3.12", "3.11", "3.10") |
        Where-Object { $installed -match "[-:]$([regex]::Escape($_))(-64)?\s" } |
        Select-Object -First 1
    if (-not $ver) {
        throw "[setup] Python 3.10 to 3.13 is required (cyclonedds has no wheel for newer ones). Installed:`n$installed"
    }
    Write-Host "[setup] creating .venv-demo with Python $ver"
    Invoke-Native "venv creation" { py "-$ver" -m venv $Venv }
}
Invoke-Native "pip upgrade" { & $Py -m pip install --quiet --upgrade pip }
Invoke-Native "TopicForge install" { & $Py -m pip install --quiet -e "$Repo[dds]" }
Invoke-Native "dust-dds install" { & $Py -m pip install --quiet "dust-dds==0.16.0" }
Write-Host "[setup] TopicForge, cyclonedds and dust-dds installed in $Venv"

if (Get-Command cargo -ErrorAction SilentlyContinue) {
    Push-Location (Join-Path $Repo "scripts\integration\publishers\dust_publisher")
    try { Invoke-Native "cargo build" { cargo build --release --quiet } } finally { Pop-Location }
    Write-Host "[setup] Rust / Dust participant built"
} else {
    Write-Host "[setup] cargo not found: install Rust (https://rustup.rs) to build the Dust participant"
}

if ($Firewall) {
    # The venv python.exe is a launcher that starts the base interpreter, so
    # both need a rule. Built demo programs are found by their known names.
    $demoExes = @("dust_publisher", "fast_publisher", "cyclone_c", "cyclone_cpp",
        "cyclone_rust", "rti_c", "rti_cpp", "ospl_publisher")
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

Invoke-Native "participant listing" { & $Py (Join-Path $Repo "scripts\integration\interop_check.py") --list }
Write-Host "[setup] done. Run: scripts\integration\launch\run_demo.ps1"
