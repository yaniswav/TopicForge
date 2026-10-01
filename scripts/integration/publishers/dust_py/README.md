# Dust DDS, Python participant

Writes `DemoHeartbeat` (`struct Heartbeat { uint32 seq; }`, RELIABLE, 1 Hz)
and nothing else. See `../../DEMO_CONTRACT.md`, section "Language
participants".

## Install

```
pip install dust-dds==0.16.0
```

`dust-dds` is published on PyPI with prebuilt wheels (PyO3 bindings of the
Rust implementation). No compiler is needed.

## Artifact

The artifact is `dust_py_publisher.py` itself, run with the interpreter that
has `dust-dds` installed:

```
python dust_py_publisher.py --domain 0
```

There is no `build.sh` / `build.ps1`: nothing to build. The driver starts
this file only when `import dust_dds` succeeds in the interpreter it uses.

## Notes

- Dust DDS discovers peers over UDP multicast only. A network or container
  that blocks multicast will not show this participant.
- API calls follow `bindings/python/README.md` and
  `bindings/python/tests/test_type_definition.py` of
  <https://github.com/s2e-systems/dust-dds> at tag `v0.16.0`. The DDS type
  name is the Python class name, hence `Heartbeat`.
