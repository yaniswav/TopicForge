# Dust DDS, C participant

Writes `DemoHeartbeat` (`struct Heartbeat { uint32 seq; }`, RELIABLE, 1 Hz)
and nothing else. See `../../DEMO_CONTRACT.md`, section "Language
participants".

## Status of the binding

`dust_dds_c` lives in `bindings/c` of <https://github.com/s2e-systems/dust-dds>
(added 2026-08-04, crate version 0.17.0, `publish = false`). It is not on
crates.io and has no release tag, so the build scripts clone the repository at
a pinned commit (`DUST_COMMIT`, head of `main` on 2026-10-01:
`74a70d7101cb1562e01c2469db62b24e7ec19a64`). Bump it deliberately.

The C types are not hand-written: `dust_dds_gen` (a crate of the same
repository) turns `Heartbeat.idl` into `Heartbeat.h`, which carries the
dynamic-type definition and the typed `HeartbeatDataWriter_write` helper.

## Build

Needs `git`, `cargo` (Rust >= 1.87, edition 2024) and a C compiler. Nothing is
installed system-wide; everything lands in `./build/` (gitignored).

- Linux: `./build.sh` (gcc, static `libdust_dds_c.a`).
- Windows: from a "Developer PowerShell for VS" (so `cl.exe` is on PATH),
  `.\build.ps1`. Set `$env:DUST_CARGO_TOOLCHAIN = "1.97.0"` first if the
  default rustup toolchain is not usable. The dynamic `dust_dds_c.dll` is
  copied next to the executable.

## Artifact

| OS | Artifact the driver starts |
|---|---|
| Linux | `build/dust_c_publisher` |
| Windows | `build/dust_c_publisher.exe` (needs `build/dust_dds_c.dll` beside it) |

```
build/dust_c_publisher --domain 0
```

Dust DDS discovers peers over UDP multicast only.

## Sources

API calls follow `bindings/c/tests/test_hello_world.c` and the cbindgen-generated
`bindings/c/include/dust_dds.h` of the pinned commit. The enum constant is
`RELIABLE_RELIABILITY_QOS` (cbindgen does not prefix enum values).
