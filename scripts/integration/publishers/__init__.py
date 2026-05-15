"""Per-vendor minimal DDS publishers for the integration rig.

Each publisher is a standalone Python script taking CLI args
`--topic`, `--rate-hz`, `--duration-s`, `--domain`, and the optional
`--gap-at-seq` for the sequence-gap scenarios. The scenarios runner
spawns these as subprocesses ; the Docker compose rig wraps the
same scripts in per-vendor containers.
"""
