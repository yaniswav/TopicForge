# Fast DDS, Python participant

Writes `DemoHeartbeat` (`struct Heartbeat { uint32 seq; }`, RELIABLE, 1 Hz)
and nothing else. See `../../DEMO_CONTRACT.md`, section "Language
participants".

This one is **Linux-first and heavy**. The `fastdds` Python module is **not a
PyPI package**: `pip install fastdds` does not give the eProsima binding. It
has to be built from <https://github.com/eProsima/Fast-DDS-python>, and the
`Heartbeat` type module has to be generated and compiled per IDL file. This
directory was written from the upstream sources and docs and has **never been
executed**.

## Prerequisites

- Fast DDS 3.x, Fast-CDR and Fast-DDS-python built and installed. The
  versions pinned by Fast-DDS-python `v2.6.2` (its `fastdds_python.repos`) are
  Fast DDS `v3.6.2`, Fast-CDR `v2.3.6`, Fast-DDS-Gen `v4.3.0`. The simplest
  route is the upstream colcon workflow:

  ```
  mkdir -p fastdds_python_ws/src && cd fastdds_python_ws
  wget https://raw.githubusercontent.com/eProsima/Fast-DDS-python/v2.6.2/fastdds_python.repos
  vcs import src < fastdds_python.repos
  colcon build --merge-install
  ```

  The repos file also checks out Fast-DDS-Gen; it still has to be built with
  Gradle (see the Fast DDS installation manual, section "Fast DDS-Gen") so that
  `fastddsgen` is on `PATH`.
- **Java >= 17** for `fastddsgen` (Fast-DDS-Gen stopped supporting older JDKs
  at v4.3.0).
- **SWIG < 4.2** (4.1 recommended), `cmake`, a C++ compiler, python3
  development headers.

## Build

```
export FASTDDS_HOME=/path/to/fastdds_python_ws/install   # prefix with fastdds, fastcdr, fastdds_python
./build.sh
```

`build.sh` runs `fastddsgen -python -replace -d build/gen Heartbeat.idl`,
builds and installs the generated CMake project (library `Heartbeat` plus the
SWIG module) into `build/install`, and writes `build/env.sh` with the
`PYTHONPATH` and `LD_LIBRARY_PATH` that `run.sh` needs. Use `PYTHON=python3.x`
to pick the interpreter; it must match the one Fast-DDS-python was built for.

## Artifact

| OS | Artifact the driver starts |
|---|---|
| Linux | `run.sh --domain N` (loads `build/env.sh`, then runs `fast_py_publisher.py`) |
| Windows | none: not supported here |

## API sources

Fast-DDS-python `v2.6.2` (tag `v2.6.2.0`, commit `647d9f4`):
`fastdds_python_examples/HelloWorldExample/HelloWorldExample.py` for the
participant, topic, publisher and writer calls, and its `generated_code/` for
the shape of the generated module (`Heartbeat.Heartbeat`,
`Heartbeat.HeartbeatPubSubType`, accessor `sample.seq(value)`);
`fastdds_python/test/api/test_qos.py` for `reliability().kind =
fastdds.RELIABLE_RELIABILITY_QOS` and `test_datawriter.py` for
`fastdds.RETCODE_OK == datawriter.write(sample)`.
