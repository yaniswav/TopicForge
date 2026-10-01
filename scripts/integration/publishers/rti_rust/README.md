# rti_rust: Rust / RTI Connector participant (demo, experimental)

Joins a DDS domain, writes `DemoHeartbeat` (`struct Heartbeat { uint32 seq; }`,
RELIABLE, 1 Hz) and nothing else, through RTI Connector for Rust
(`rticonnextdds-connector-rust`, tag `v1.5.0`). That crate is experimental and
RTI says not to use it in production; this is a demo node only. Contract:
`../../DEMO_CONTRACT.md` ("Language participants"). Local only, never in public
CI. No performance measurement (see "Legal reminders" in `../RTI.md`).

Start line: `[rti_rust] domain N: writes DemoHeartbeat (RELIABLE)`.

## How it is configured

The Connector builds its entities from an XML application definition:
`heartbeat.xml` (type `Heartbeat`, topic `DemoHeartbeat`, one RELIABLE writer).
The domain id lives in that XML, so the file is a template with the token
`__DOMAIN_ID__`. The program embeds it, replaces the token with the `--domain`
value (default 0) and passes the document to `Connector::new` as an XML string
(`str://"<dds>...</dds>"` URL form, no temporary file). That URL form is
documented for the same native library by the Python and JavaScript
Connectors; it has not been run through the Rust crate. If it is rejected, edit
`domain_id` in a copy of `heartbeat.xml` and pass the file path to
`Connector::new` instead.

## Prerequisites

- Rust 1.85 or newer (the Connector crate uses edition 2024) and network access
  at build time: cargo fetches the git dependency and `build.rs` downloads the
  native libraries (`connectorlibs-1.5.0.zip`, about 25 MB) from
  `rticonnextdds-connector` release `v1.5.0`. Supported: Windows x64, Linux
  x64 and arm64, macOS arm64. No RTI Connext install is needed to build.
- Licensing: the crate states that a valid RTI Connext Professional license
  governs its use, otherwise RTI's Non-Commercial license #4040 applies. Whether
  the native libraries need `rti_license.dat` at run time is not documented
  there; set `RTI_LICENSE_FILE` (full path with file name) as for the other
  RTI participants, and never commit the file. See `../RTI.md`.

## Build

```
# Linux
./build.sh
```

```
# Windows (PowerShell)
.\build.ps1
```

Artifact: `target/release/rti_rust` (Linux), `target\release\rti_rust.exe`
(Windows). The scripts copy the native libraries (extracted by `build.rs` under
`target/release/build/rtiddsconnector-*/out/lib/<arch>/`) next to the
executable.

## Run

```
# Linux
export LD_LIBRARY_PATH=$PWD/target/release:$LD_LIBRARY_PATH
export RTI_LICENSE_FILE=/path/to/rti_license.dat
./target/release/rti_rust --domain 0
```

```
# Windows (PowerShell): the DLLs next to the exe are found without PATH changes
$env:RTI_LICENSE_FILE = "C:\path\to\rti_license.dat"
.\target\release\rti_rust.exe --domain 0
```

It runs until killed (Ctrl+C or SIGTERM end the process). If the Connector
cannot create the participant (typically a missing or exhausted license), it
prints a message mentioning `RTI_LICENSE_FILE` on stderr and exits with status 1.
