# cyclone_cpp

Cyclone DDS (C++, cyclonedds-cxx 11.0.x) demo participant. Joins
`--domain N` (default 0) and writes `DemoHeartbeat`
(`struct Heartbeat { uint32 seq; }`, RELIABLE, 1 Hz). Prints one start line and
runs until SIGINT/SIGTERM or kill.
Contract: `../../DEMO_CONTRACT.md`, section "Language participants".

Not built or run in the repo's CI; the code is written from the cyclonedds-cxx
11.0.1 `examples/helloworld` and `examples/throughput` sources.

## Prerequisites

- CMake >= 3.16 and a C++17 compiler.
- Cyclone DDS 11.0.1 (C library with `idlc`) and cyclonedds-cxx 11.0.1 (with
  `idlcxx`), both installed. Build the C library first, install it, then build
  cyclonedds-cxx with `-DCMAKE_PREFIX_PATH=<c-install>`:
  - Linux: from source, or Conan.
  - Windows: from source with Visual Studio, or Conan.
- `CYCLONEDDS_HOME` (or `CMAKE_PREFIX_PATH`) listing the install prefix(es). If
  the C and C++ libraries live in separate prefixes, join them with `;`.

## Build

```
CYCLONEDDS_HOME=$HOME/cyclonedds-install ./build.sh        # Linux
$env:CYCLONEDDS_HOME = 'C:\cyclonedds-install'; .\build.ps1  # Windows
```

## Artifact

- Linux: `build/cyclone_cpp`
- Windows (MSVC multi-config): `build/Release/cyclone_cpp.exe`

## Run

```
build/cyclone_cpp --domain 0
```

Runtime: on Windows the directories holding `ddsc.dll` and `ddscxx.dll` (the
`bin` folder of each install prefix) must be on `PATH`. On Linux the CMake
build tree embeds an RPATH; if the loader still cannot find `libddsc.so` or
`libddscxx.so`, add the `lib` folder(s) to `LD_LIBRARY_PATH`.
