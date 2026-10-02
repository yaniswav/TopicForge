# OpenSplice participant (C / SAC) - EXPERIMENTAL

**EXPERIMENTAL. Nothing here has ever been compiled or run.** Vortex OpenSplice
Community Edition has been abandoned since its last release (6.9.210323OSS,
2021-03-23). This participant exists to check that TopicForge sees an
OpenSplice endpoint through standard RTPS discovery (vendor id 01.02). It is
the most likely part of the multi-vendor demo to fail, and the demo does not
depend on it.

Cyclone DDS is the official successor of OpenSplice at ADLINK (now ZettaScale).
Nothing here should be used for anything but this demo.

What it does: joins one DDS domain, writes `DemoStatus` (type `Status`,
`{ unsigned long seq; string text; }`) at 10 Hz, RELIABLE, VOLATILE,
KEEP_LAST 1, and prints one `[ospl_publisher] ...` line once ready. Nobody
reads it, so TopicForge must show a publication and no QoS mismatch. The
contract is `../../DEMO_CONTRACT.md`.

The registered DDS type name is exactly `Status`: `Status.idl` has no module.

## Why prebuilt binaries only

Compiling OpenSplice from source fails on current toolchains (upstream issues
#169 for GCC 10 and #185). Do not try it. Use the prebuilt HDE (Host
Development Environment) attached to the GitHub release
`OSPL_V6_9_210323OSS_RELEASE` of `ADLINK-IST/opensplice`. It provides `idlpp`
(IDL to C generator), the C "SAC" API and the runtime libraries.

## Install the HDE

Release assets are named `PXXX-VortexOpenSplice-6.9.210323OSS-HDE-<target>`.

Linux (x86_64):

1. Download `...HDE-x86_64.linux-gcc7-glibc2.27-installer.tar`. If the binaries
   refuse to start on your distribution, try the `gcc5.5.0-glibc2.23` asset.
2. Extract the tar and run the installer it contains (an interactive or
   `--mode unattended` installer, check its `--help`). The HDE ends up in a
   directory like `<prefix>/HDE/x86_64.linux`.
3. Load the environment in the shell you build and run from:
   `source <prefix>/HDE/x86_64.linux/release.com` (must be sourced from bash).
   It sets `OSPL_HOME`, `PATH`, `LD_LIBRARY_PATH` and `OSPL_URI`.
4. `gcc` and `cpp` must be installed, `idlpp` uses the C preprocessor.

Windows (x64):

1. Download `...HDE-x86_64.win-vs2019-installer.zip`, extract it and run the
   installer. The HDE ends up in a directory like `<prefix>\HDE\x86_64.win64`.
2. Open an "x64 Native Tools Command Prompt for VS 2019" (or later), which puts
   `cl.exe` on PATH.
3. `call <prefix>\HDE\x86_64.win64\release.bat`
4. The VC++ 2019 redistributable must be installed to run the result.

The exact installer steps and directory names above are from the release
asset names and the upstream install scripts, not from a run.

## Build

With `OSPL_HOME` set by `release.com` / `release.bat`:

    ./build.sh          # Linux, output build/ospl_publisher
    build.bat           # Windows, output build\ospl_publisher.exe

Both run `idlpp -S -l c Status.idl` (standalone C mode) into `build/`, then
compile the generated type support and `src/ospl_publisher.c` against the SAC
libraries of the HDE.

## Run

    OSPL_HOME=<HDE dir> ./run_ospl.sh [--domain N]
    set OSPL_HOME=<HDE dir> & run_ospl.bat [--domain N]

The scripts load `release.com` / `release.bat` from `OSPL_HOME`, default
`OSPL_URI` to `etc/config/ospl_sp_ddsi.xml` when it is unset, then start the
binary with the remaining arguments. That configuration is a SingleProcess
domain whose `ddsi2` service speaks RTPS with multicast enabled, which is what
makes the participant visible to Cyclone and TopicForge.

### Choosing the domain

OpenSplice reads the domain from the configuration, not from the program:
`Domain/Id` in the XML named by `OSPL_URI` (0 in `ospl_sp_ddsi.xml`). The
program always creates its participant with `DDS_DOMAIN_ID_DEFAULT`.

- `--domain N` with N other than 0 makes the run scripts write a copy of the
  XML with `<Id>N</Id>` into `build/` and point `OSPL_URI` at it.
- If you set `OSPL_URI` yourself, `--domain` is ignored: edit `Domain/Id` in
  your own file.

That `Domain/Id` maps to the RTPS domain id used by `ddsi2` is expected, not
verified.

## Abandon criterion

Try it once, and stop there:

- If the 2021 binaries do not start (glibc too old or too new, missing VC++
  redistributable, installer failure), or
- if they start but the participant does not appear in TopicForge's
  `list_participants` within a few seconds on the same domain and network,

then record the result (OS, HDE asset, error text) in the demo notes and
**remove OpenSplice from the demo**. Do not compile OpenSplice from source and
do not fall back to another OpenSplice build. The demo is valid without it:
the driver starts a participant only when its artifact exists.

## Layout

| File | Role |
|---|---|
| `Status.idl` | Type definition, no module |
| `src/ospl_publisher.c` | The participant |
| `build.sh`, `build.bat` | idlpp plus compile and link |
| `run_ospl.sh`, `run_ospl.bat` | Environment, `OSPL_URI`, launch |
| `build/` | Generated, ignored by git |

## API reference used

`src/ospl_publisher.c` is modelled on the official example
`examples/dcps/HelloWorld/c/src/` (`HelloWorldDataPublisher.c`,
`DDSEntitiesManager.c`, `CheckStatus.c`) of the upstream repository. The
type-support function names follow the idlpp SAC templates in
`etc/idlpp/SAC/` of the same repository.
