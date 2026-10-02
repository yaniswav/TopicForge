# cyclone_rust

Cyclone DDS (Rust) demo participant using the official crate
`eclipse-cyclonedds` 0.0.4. Joins `--domain N` (default 0) and writes
`DemoHeartbeat` (`struct Heartbeat { uint32 seq; }`, type name `Heartbeat`,
RELIABLE, 1 Hz). Prints one start line and runs until Ctrl+C/SIGINT/SIGTERM
or kill. Contract: `../../DEMO_CONTRACT.md`, section "Language participants".

Not compiled yet: the last attempt stopped in the crate's build script because
`libclang` was missing (see below). The code is written from the crate's README
and `examples/pub.rs` at tag 0.0.4.

## Prerequisites

- Rust stable (edition 2021 here; the crate itself uses edition 2024, so a
  recent toolchain is needed).
- `libclang` for bindgen (Linux: `libclang-dev`; Windows: LLVM, set
  `LIBCLANG_PATH` to the folder holding `libclang.dll`).
- Either:
  - an installed Cyclone DDS 11.0.x with headers, `CYCLONEDDS_HOME` set to its
    prefix (default), or
  - `--features vendored`, which builds the sources bundled in the crate and
    needs `cmake` and a C compiler.

## Build

```
CYCLONEDDS_HOME=$HOME/cyclonedds-install ./build.sh        # Linux
$env:CYCLONEDDS_HOME = 'C:\cyclonedds-install'; .\build.ps1  # Windows
./build.sh --features vendored                              # bundled sources
```

## Artifact

- Linux: `target/release/cyclone_rust`
- Windows: `target\release\cyclone_rust.exe`

## Run

```
target/release/cyclone_rust --domain 0
```

Runtime with an installed (non-vendored) Cyclone DDS: Linux needs
`$CYCLONEDDS_HOME/lib` on `LD_LIBRARY_PATH` (Cargo does not embed an RPATH);
Windows needs `%CYCLONEDDS_HOME%\bin` (holding `ddsc.dll`) on `PATH`. A
vendored build is not verified yet; check whether it still needs `ddsc` on the
loader path.
