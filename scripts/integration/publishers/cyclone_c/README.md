# cyclone_c

Cyclone DDS (C, libddsc 11.0.x) demo participant. Joins `--domain N`
(default 0) and writes `DemoHeartbeat` (`struct Heartbeat { uint32 seq; }`,
RELIABLE, 1 Hz). Prints one start line and runs until SIGINT/SIGTERM or kill.
Contract: `../../DEMO_CONTRACT.md`, section "Language participants".

Not built or run in the repo's CI; the code is written from the Cyclone DDS
11.0.1 `examples/helloworld` sources.

## Prerequisites

- CMake >= 3.16 and a C compiler.
- Cyclone DDS 11.0.1 installed, with `idlc` (default build option):
  - Linux: build from source (`cmake -DCMAKE_INSTALL_PREFIX=$HOME/cyclonedds-install ..`,
    then `cmake --build . --target install`), or install through Conan.
  - Windows: same from source with Visual Studio (Developer PowerShell), or Conan.
- `CYCLONEDDS_HOME` (or `CMAKE_PREFIX_PATH`) pointing at the install prefix.

## Build

```
CYCLONEDDS_HOME=$HOME/cyclonedds-install ./build.sh        # Linux
$env:CYCLONEDDS_HOME = 'C:\cyclonedds-install'; .\build.ps1  # Windows
```

## Artifact

- Linux: `build/cyclone_c`
- Windows (MSVC multi-config): `build/Release/cyclone_c.exe`

## Run

```
build/cyclone_c --domain 0
```

Runtime: on Windows the directory holding `ddsc.dll` (`%CYCLONEDDS_HOME%\bin`)
must be on `PATH`. On Linux the CMake build tree embeds an RPATH to the install
prefix; if the loader still cannot find `libddsc.so`, add
`$CYCLONEDDS_HOME/lib` to `LD_LIBRARY_PATH`.
