# fast_publisher (C++ / Fast DDS 3)

Fast DDS participant of the multi-vendor demo. It writes `DemoImu`
(BEST_EFFORT, 10 Hz) and reads `DemoOdom` (BEST_EFFORT). The contract is in
`../../DEMO_CONTRACT.md`. Types are built at runtime with the Fast DDS 3 dynamic
XTypes API, so neither `fastddsgen` nor Java is needed.

Requires Fast DDS 3.x and Fast CDR 2.x (not Fast DDS 2.x / `fastrtps`).

## Install Fast DDS 3

Linux. `libfastdds-dev` does not exist in Ubuntu 24.04 and the distro
packages stop at 2.x, so use one of:

- the eProsima binary installer for Fast DDS 3 (see the Linux installation page of the Fast DDS documentation),
- a source build of `foonathan_memory_vendor`, `Fast-CDR` and `Fast-DDS` into a
  prefix (see `.github/workflows/demo-fast.yml` for a pinned, working recipe;
  needs `libasio-dev libtinyxml2-dev libssl-dev`).

Windows. The eProsima installer, or `vcpkg install fastdds`. Make sure the
Fast DDS and Fast CDR DLL directories are on `PATH` when running.

## Build

```
cmake -S . -B build -DCMAKE_PREFIX_PATH=<install prefix, if not default>
cmake --build build --config Release
```

The binary is `build/fast_publisher` on Linux and `build/Release/fast_publisher.exe`
on Windows (MSVC). Run it with `--domain N` (default 0); it prints one
`[fast_publisher] ...` line once its entities exist and runs until killed.
