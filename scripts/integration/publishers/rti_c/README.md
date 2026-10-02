# rti_c: C / RTI Connext participant (demo)

Joins a DDS domain, writes `DemoHeartbeat` (`struct Heartbeat { uint32 seq; }`,
RELIABLE, 1 Hz) and nothing else. Contract: `../../DEMO_CONTRACT.md`
("Language participants"). Local only: it needs a licensed RTI Connext
Professional install, which is never redistributed or committed. No performance
measurement (see "Legal reminders" in `../RTI.md`).

Start line: `[rti_c] domain N: writes DemoHeartbeat (RELIABLE)`.

## Prerequisites

- RTI Connext Professional 7.x: the host bundle (provides `rtiddsgen`) and the
  target bundle for your platform (headers and libraries), from RTI.
- A license file (`rti_license.dat`), see `../RTI.md` for the Connext Express
  option. Point Connext at it with `RTI_LICENSE_FILE` (full path with file
  name). Never commit it.
- `NDDSHOME` set to the install directory.
- CMake >= 3.11 and a C compiler: GCC or Clang on Linux, Visual Studio on
  Windows (use a Visual Studio matching one of the installed target archs,
  for example `x64Win64VS2017`).

## Build

```
# Linux
export NDDSHOME=$HOME/rti_connext_dds-7.3.0
export CONNEXTDDS_ARCH=x64Linux4gcc7.3.0    # only if several archs are installed
./build.sh
```

```
# Windows (PowerShell)
$env:NDDSHOME = "C:\Program Files\rti_connext_dds-7.3.0"
$env:CONNEXTDDS_ARCH = "x64Win64VS2017"     # only if several archs are installed
.\build.ps1
```

Artifact: `build/rti_c` (Linux), `build\Release\rti_c.exe` (Windows). `rtiddsgen`
generates the type code from `Heartbeat.idl` into `build/src`.

## Run

The shared Connext libraries must be found at run time:

```
# Linux
export LD_LIBRARY_PATH=$NDDSHOME/lib/$CONNEXTDDS_ARCH:$LD_LIBRARY_PATH
export RTI_LICENSE_FILE=/path/to/rti_license.dat
./build/rti_c --domain 0
```

```
# Windows (PowerShell)
$env:PATH = "$env:NDDSHOME\lib\$env:CONNEXTDDS_ARCH;$env:PATH"
$env:RTI_LICENSE_FILE = "C:\path\to\rti_license.dat"
.\build\Release\rti_c.exe --domain 0
```

It runs until killed (Ctrl+C or SIGTERM). If entity creation fails (typically a
missing or exhausted license), it prints a message mentioning
`RTI_LICENSE_FILE` on stderr and exits with status 1.
