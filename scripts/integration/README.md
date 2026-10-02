# TopicForge multi-vendor demo

One read-only TopicForge participant observes programs from several DDS
vendors and several languages on the same domain. It sees them through the
standard RTPS discovery topics, with no vendor-specific binding. The questions
are asked by the official MCP client over stdio, the same path Claude Desktop
or Claude Code uses, so the demo exercises the server exactly as an agent
would.

This is the first thing in the repository that runs the DDS adapters against
live traffic. The status of each participant is listed below, and most of them
have not been run yet.

TopicForge never publishes. The participants in `publishers/` are demo
programs written for this directory; TopicForge itself only reads discovery
data.

## Quick start

Windows (PowerShell):

```powershell
scripts\integration\launch\setup.ps1
scripts\integration\launch\run_demo.ps1
```

Linux or WSL:

```bash
scripts/integration/launch/setup.sh
scripts/integration/launch/run_demo.sh
```

`setup` creates `.venv-demo` at the repo root, installs TopicForge with the
Cyclone binding (`pip install -e ".[dds]"`) and builds the Rust / Dust
participant if `cargo` is on PATH. It ends by printing what can run on the
host. `run_demo` starts every participant that is available, runs the checks
and stops every process it started, including on error or Ctrl+C.

To see what would start without starting anything:

```bash
python scripts/integration/interop_check.py --list
```

Each participant is reported as `ready` or `skipped (<what is missing>)`.
`--domain N` selects another DDS domain (default 0, or
`TOPICFORGE_DDS_DOMAIN_ID`).

### The minimum

Two participants are required, and the driver exits with an error without
them:

- Python / Cyclone: `pip install "topicforge[dds]"`.
- Rust / Dust: `cargo build --release` in `publishers/dust_publisher`.

Every other participant is optional. It joins the bus automatically when its
artifact is present, and the checks adapt to what was actually started.

## Participants

Twelve programs, one per vendor and language that has an officially released
binding. The
contract they all follow (topics, types, QoS, start line) is in
[`DEMO_CONTRACT.md`](DEMO_CONTRACT.md). The five scenario programs carry the
QoS story; the seven language participants each write one topic,
`DemoHeartbeat`, so that `list_participants` shows one entry per vendor and
language.

Status is stated as of this writing. "Tested" means it ran on the bus and was
seen by TopicForge, on Windows 11 only. "Written, not run" means the source
exists and was written from the vendor's documentation or example sources, but
nobody has built or started it.

| Vendor | Language | Directory under `publishers/` | Role | Status |
|---|---|---|---|---|
| Cyclone DDS | Python | `cyclone_publisher.py` | writes `DemoOdom`, reads `DemoLidarScan` | Tested |
| Dust DDS | Rust | `dust_publisher/` | writes `DemoLidarScan` | Tested |
| Dust DDS | Python | `dust_py/` | `DemoHeartbeat` | Tested |
| Fast DDS | C++ | `fast_publisher_cpp/` | writes `DemoImu`, reads `DemoOdom` | Written, not run |
| Fast DDS | Python | `fast_py/` | `DemoHeartbeat` | Written, not run (Linux only, built from source) |
| RTI Connext | Python | `rti_publisher.py` | writes `DemoHeartbeat`, reads `DemoImu` | Written, not run (local, license) |
| RTI Connext | C | `rti_c/` | `DemoHeartbeat` | Written, not run (local, license) |
| RTI Connext | C++ | `rti_cpp/` | `DemoHeartbeat` | Written, not run (local, license) |
| Cyclone DDS | C | `cyclone_c/` | `DemoHeartbeat` | Written, not run |
| Cyclone DDS | C++ | `cyclone_cpp/` | `DemoHeartbeat` | Written, not run |
| Cyclone DDS | Rust | `cyclone_rust/` | `DemoHeartbeat` | Written, not run (first build stopped on a missing `libclang`) |
| OpenSplice | C | `opensplice_publisher/` | writes `DemoStatus` | Written, not run (experimental) |

`.github/workflows/demo-fast.yml` builds the Fast DDS C++ participant and
starts it for ten seconds. It has no run to show yet.

Four vendor and language combinations have no binding at all and are not
covered: Fast DDS C, Fast DDS Rust, Dust DDS C++ and OpenSplice Rust. Two
have a binding that is not an official release and are left out on purpose:
the RTI Connector for Rust (experimental, not on crates.io) and the Dust DDS C
binding (unpublished).
OpenSplice C++ and Python exist, but the project has had no release since
2021, so the C program alone stands for OpenSplice.

Each directory has its own README with prerequisites, build commands and the
exact artifact path the driver looks for. RTI participants also have
[`publishers/RTI.md`](publishers/RTI.md) for licensing.

## What the driver checks

The driver starts TopicForge with `TOPICFORGE_MODE=live` and
`TOPICFORGE_DDS_BACKEND=cyclone`, waits six seconds for discovery announcements
to cross the bus, then calls four tools and checks the answers against the
participants it actually started:

1. `list_participants`: at least one entry per started program plus
   TopicForge itself, and a participant tagged with the expected vendor for
   every vendor that TopicForge can identify (see the known limit below).
2. `detect_qos_mismatches`: a Reliability mismatch on `DemoLidarScan` (Dust
   BEST_EFFORT writer, Cyclone RELIABLE reader) and, when both are running, on
   `DemoImu` (Fast DDS BEST_EFFORT writer, RTI RELIABLE reader). `DemoOdom` is
   compatible by design and must not be reported.
3. Departure: it stops the Python / Cyclone participant and polls
   `list_participants` for up to 40 seconds until that participant shows as
   `left`, which happens when its lease expires.
4. `participant_events`: prints the discovered and lost timeline.

It prints `result: PASS` and exits 0, or prints each `FAIL:` line and exits 1.
With only the two required participants, the Reliability check on
`DemoLidarScan` and the departure check are the ones that apply.

## Known limits

- **Vendor shown as `unknown` for Dust DDS and RTI Connext.** TopicForge reads
  the vendor from the first two bytes of the participant GUID prefix, because
  the Python Cyclone binding does not expose the vendor id from the RTPS
  header. Dust DDS does not prefix its GUID with its vendor id, and RTI does
  not by default. The participants are still listed, only the vendor tag is
  missing. The driver does not require a tag for them.
- **OpenSplice is experimental.** The last release is from 2021, it cannot be
  compiled on current toolchains, and the participant needs the prebuilt HDE
  (`OSPL_HOME`). Its README sets an abandon criterion: try it once, and drop it
  from the demo if it does not appear on the bus.
- **RTI runs locally only.** A license is required (`RTI_LICENSE_FILE`). The
  driver skips every RTI participant when that variable is unset, and RTI never
  runs in public CI. RTI's Free Use license (agreement #4046) forbids
  disclosing evaluation results without RTI's prior written consent. No
  capture, screenshot, recording or result from an RTI run is published
  without that consent, and there is no benchmarking with these nodes.
- **Dust DDS discovers over multicast only.** A network that blocks multicast
  will not show Dust participants.
- **User-topic payloads are not read.** The demo shows who is on the bus and
  which pairs cannot communicate. It does not show message contents; that
  decoding is disabled in the core (see the CHANGELOG, 0.5.3).
- **Participants and observer run on one host by default.** The driver starts
  everything locally. Running across machines is the next section.

## Mixed Linux and Windows bus

Multicast discovery often does not cross between two machines (WSL in NAT mode,
Docker Desktop, some Wi-Fi networks, the Windows firewall). The recommended
layout, per-vendor unicast peer settings, the Windows firewall rules and the WSL
caveats are in [`config/MIXED_BUS.md`](config/MIXED_BUS.md). Peer files for
Cyclone and Fast DDS are in `config/`. Participants on the second machine are
started by hand; the driver does not launch remote processes.

## CI

- `.github/workflows/demo.yml` runs the driver with the Python / Cyclone and
  Rust / Dust participants on `ubuntu-latest` and `windows-latest`. It triggers
  on pushes and pull requests that touch `scripts/integration/**`,
  `src/topicforge/adapters/**` or the workflow, and on manual dispatch. RTI and
  OpenSplice never run there.
- `.github/workflows/demo-fast.yml` builds Fast DDS 3 from pinned tags, builds
  the C++ participant and starts it for ten seconds. Weekly (Monday) and manual
  only, because the cold build is long. It checks that the participant starts,
  not that TopicForge sees it.

A green `demo.yml` run shows that Cyclone and Dust participants are discovered
and that one QoS mismatch is reported. It says nothing about Fast DDS, RTI or
OpenSplice.

## Related

- [`DEMO_CONTRACT.md`](DEMO_CONTRACT.md): topics, types, QoS, directory layout.
- [`docs/DDS_QUICKSTART.md`](../../docs/DDS_QUICKSTART.md): using the DDS tools
  from an MCP client.
- [`docs/dds-interop-matrix.md`](../../docs/dds-interop-matrix.md): the
  multi-vendor positioning and its limits.
