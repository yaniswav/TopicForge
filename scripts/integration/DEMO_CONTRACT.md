# Multi-vendor demo: shared contract

Every demo participant follows this contract so that the driver
(`interop_check.py`) can start any subset of them and know what to
expect. TopicForge itself never publishes; it only reads DDS discovery.

## Common rules

- One program per vendor and language. It joins `--domain N` (default 0),
  prints exactly one line starting with `[<name>]` once its entities exist,
  then runs until it is killed. It never exits on its own.
- No interactive prompt, no file written outside its own build directory.
- Pure ASCII source and output.

## Topics, types and QoS

Type names and field layouts must be identical across vendors so that DDS
itself would match the endpoints if the QoS allowed it. TopicForge pairs
endpoints by topic name only.

| Topic | Type (IDL) | Writer | Reader | Expected |
|---|---|---|---|---|
| `DemoLidarScan` | `struct LidarScan { uint32 seq; float range_m; };` | Rust / Dust, BEST_EFFORT | Python / Cyclone, RELIABLE | Reliability mismatch |
| `DemoOdom` | `struct Odom { uint32 seq; double x; double y; };` | Python / Cyclone, RELIABLE | C++ / Fast DDS, BEST_EFFORT | Compatible, no report |
| `DemoImu` | `struct Imu { uint32 seq; double yaw_rad; };` | C++ / Fast DDS, BEST_EFFORT | Python / RTI Connext, RELIABLE | Reliability mismatch |
| `DemoStatus` | `struct Status { uint32 seq; string text; };` | C / OpenSplice, RELIABLE | none | Visible publication only |
| `DemoHeartbeat` | `struct Heartbeat { uint32 seq; };` | RTI Python and every language participant below, RELIABLE | none | Visible publication only |

All other QoS stay at their defaults (VOLATILE durability, KEEP_LAST 1).
Publish rate: 10 Hz, except `DemoHeartbeat` at 1 Hz. The heartbeat gives a
participant a publication of its own whatever else runs.

## Language participants

The five programs above carry the scenario. Smaller programs cover the vendor x
language grid: each joins the bus, write `DemoHeartbeat`
(RELIABLE, 1 Hz) and nothing else, so that `list_participants` shows one
participant per vendor and language. Same rules: `--domain N`, one start line
`[<dir name>] domain N: writes DemoHeartbeat (RELIABLE)`, run until killed.

| Vendor | Language | Directory under `publishers/` | Status |
|---|---|---|---|
| Cyclone DDS | C | `cyclone_c/` | official |
| Cyclone DDS | C++ | `cyclone_cpp/` | official |
| Cyclone DDS | Rust | `cyclone_rust/` | official, crate 0.0.x |
| Dust DDS | Python | `dust_py/` | official |
| RTI Connext | C | `rti_c/` | official, license needed |
| RTI Connext | C++ | `rti_cpp/` | official, license needed |
| Fast DDS | Python | `fast_py/` | official, built from source |

Combinations with no binding at all: Fast DDS C, Fast DDS Rust, Dust C++,
OpenSplice Rust. Combinations with a binding that is not officially released
are left out on purpose: RTI Connector for Rust (experimental, RTI says not
for production, not on crates.io) and the Dust C binding (unpublished).
OpenSplice C++ and Python exist but the project stopped in 2021, so the C
program alone represents it. Each directory ships `build.sh`
and `build.ps1` (or needs no build) and a README naming the artifact.

## Programs and where the driver finds them

| Vendor | Language | Source | Built artifact the driver starts |
|---|---|---|---|
| Cyclone DDS | Python | `publishers/cyclone_publisher.py` | the same file, with the current interpreter |
| Dust DDS | Rust | `publishers/dust_publisher/` | `target/release/dust_publisher[.exe]` |
| Fast DDS | C++ | `publishers/fast_publisher_cpp/` | `build/fast_publisher`, or `build/Release/fast_publisher.exe` |
| RTI Connext | Python | `publishers/rti_publisher.py` | the same file; needs `rti.connext` installed and `RTI_LICENSE_FILE` set |
| OpenSplice | C | `publishers/opensplice_publisher/` | `run_ospl.sh` / `run_ospl.bat`, which source the OpenSplice environment from `OSPL_HOME` |

The driver starts a participant only when its artifact (and, for RTI and
OpenSplice, its environment) is present, and adapts its checks to the
participants it actually started.
