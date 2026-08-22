# Troubleshooting TopicForge

Common errors, what they mean, and the remediation path. Each section
mirrors a polished `AdapterError` message from v0.5.0 so you can search
for a substring of the error text and land in the right place.

If your situation isn't covered here, open an issue at
[github.com/yaniswav/TopicForge/issues](https://github.com/yaniswav/TopicForge/issues)
: the troubleshooting list grows from real reports.

---

## "DDS observability only" / `DDS_ONLY_ERROR_MSG`

**Full message** (paraphrased):

> This adapter serves DDS observability only: it cannot run the ROS2
> graph tools (`list_topics`, `get_topic_info`, `sample_messages`,
> `analyze_bag`, `peek_bag_samples`). To get both surfaces in one
> process: install ROS2 and source the workspace so `ros2` is on PATH,
> then re-run with `TOPICFORGE_MODE=live`: the v0.4.0 CompositeAdapter
> routes ROS2 tools to the CLI and DDS tools to your
> `TOPICFORGE_DDS_BACKEND` automatically. For offline development use
> `TOPICFORGE_MODE=mock`.

**What it means.** You called a ROS2 graph tool but only a DDS adapter
is active in this process. In v0.4.0 the `CompositeAdapter` is the
intended way to get both surfaces simultaneously, and it only engages
when **both** the ROS2 CLI **and** the chosen DDS backend can be
brought up.

**How to fix.**

1. Confirm `ros2 --help` works in the same shell that spawns TopicForge
   (sourcing `/opt/ros/<distro>/setup.bash` on Linux ; running
   `call C:\dev\ros2_humble\local_setup.bat` on Windows native ;
   ensuring WSL has ROS2 sourced if you're going through WSL).
2. Re-run with `TOPICFORGE_MODE=live` and your existing
   `TOPICFORGE_DDS_BACKEND`. The factory will detect both halves and
   construct a `CompositeAdapter`.
3. Inspect `health_check`: `ros_backend` should now be `"ros2_cli"`
   and `dds_backend` should be your selected vendor.

If you genuinely want a DDS-only deployment (no ROS2), use
`TOPICFORGE_MODE=mock` for offline development or stick with the DDS
adapter and only call the 6 DDS / observability tools
(`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`,
`participant_events`, `topic_metrics`, `peek_bag_samples`).

---

## "rosbags" / `_ROSBAGS_REQUIRED_MSG`

**Full message** (paraphrased):

> Bag analysis with full sample decode requires the `rosbags` library.
> Install via `pip install topicforge[bags]` and retry. `analyze_bag`
> may still fall back to the v0.3.0 `ros2 bag info` text-parse path via
> the live ROS2 CLI adapter ; `peek_bag_samples` has no fallback.

**What it means.** You called `peek_bag_samples` (or `analyze_bag` on
a non-CLI path) without the optional `rosbags` Apache-2.0 library
installed. The library is intentionally optional: base
`pip install topicforge` keeps the install footprint small.

**How to fix.**

```bash
pip install topicforge[bags]
```

This pulls `rosbags>=0.9` (pure-Python, no native deps, works on all
supported Python/OS combos). Re-run the tool: no env-var change
needed.

If you cannot install rosbags (sandboxed CI, restricted package
allowlist, etc.), `analyze_bag` still works via the v0.3.0 `ros2 bag
info` text-parse path when run through `Ros2CliAdapter` ; the enriched
fields populate at safe defaults. `peek_bag_samples` is rosbags-only.

---

## "CycloneDDS participant discovery failed"

**Full message** (paraphrased):

> CycloneDDS participant discovery failed on domain `{id}` ({exception
> class}: {exception message}). Common causes: DDS domain mismatch,
> firewall blocking RTPS multicast, or `CYCLONEDDS_URI` pointing at an
> unreadable config.

**Diagnostics, in order.**

1. **Domain mismatch.** TopicForge joins the domain set by
   `TOPICFORGE_DDS_DOMAIN_ID` (default `0`). Your publishers must be
   on the same domain. Check with `echo $ROS_DOMAIN_ID` or by reading
   the publisher's config: they must match TopicForge's domain id.
2. **Firewall / multicast.** RTPS uses multicast on `239.255.0.x` by
   default. If your firewall blocks multicast (common on corp Wi-Fi),
   discovery times out silently. Either allow multicast on the
   interface (`sudo iptables -A INPUT -d 224.0.0.0/4 -j ACCEPT` on
   Linux for a quick test) or configure CycloneDDS for unicast
   discovery via `CYCLONEDDS_URI`.
3. **`CYCLONEDDS_URI` misconfigured.** If you've set this env var, make
   sure it points to a readable XML file. `unset CYCLONEDDS_URI` to
   confirm whether the var itself is the culprit.

The error message carries the underlying Python exception type and
text: that's usually the most informative starting point. A `Timeout`
exception points at #1 or #2 ; a `FileNotFoundError` or `OSError`
points at #3.

---

## "Fast DDS DomainParticipant creation returned None"

**Full message** (paraphrased):

> Fast DDS DomainParticipant creation returned None on domain `{id}`.
> Likely an ABI mismatch between the `fastdds` Python binding and the
> installed Fast DDS core library: pin `fastdds>=2.6.1,<3` and
> reinstall, or check the `FastDDS_DEFAULT_PROFILES_FILE` env var if
> you set one.

**What it means.** `DomainParticipantFactory.create_participant()`
returned `None` instead of raising. Almost always an ABI mismatch
between the Python `fastdds` wheel and the native `libfastdds` shared
library on the host.

**How to fix.**

1. Reinstall with the pinned version :
   ```bash
   pip uninstall -y fastdds
   pip install "fastdds>=2.6.1,<3"
   ```
2. If you set `FastDDS_DEFAULT_PROFILES_FILE`, unset it and retry: a
   broken profile XML triggers the same symptom.
3. If you have a system-wide Fast DDS native install (CMake / vcpkg /
   apt), make sure `LD_LIBRARY_PATH` (or `PATH` on Windows) does not
   conflict with the pip wheel's bundled shared library.

Fast DDS 3.x binding wheels are not yet stable on Python 3.11+ for
Windows / Linux as of v0.4.0 ; TopicForge pins to the 2.6.x line in
`pyproject.toml`. Stick with that pin until you see the v0.6 CHANGELOG
note bumping it.

---

## "domain_id must be in 0..232"

**What it means.** DDS domain ids are spec-bounded to `[0, 232]`. You
passed something outside that range.

**How to fix.** Pass an int in range. `0` is the ROS2 default ; most
production setups use `<= 100`.

---

## "lookback_seconds must be in 1..86400"

**What it means.** `participant_events` accepts a window from 1 second
to 86400 seconds (24 hours). The hard cap on the lifecycle ring buffer
is 200 events newest-first regardless of window, so very long windows
on a busy bus may not surface the oldest events.

**How to fix.** Pass a value in range, or omit the argument to take
the 300 s default.

---

## "window_seconds must be in 1..3600"

**What it means.** `topic_metrics` accepts a window from 1 second to
3600 seconds (1 hour). The `MetricsBuffer` cap is
`MAX_SAMPLES_PER_TOPIC=1000` drop-oldest, which on a 1 kHz topic
covers about 1 second of data: be aware that the observed frequency
is computed from buffered samples, not from a true rolling time
window.

**How to fix.** Pass a value in range, or omit the argument to take
the 60 s default.

---

## "count must be >= 0"

Trivial: the parameter accepts non-negative integers only. Most
sampling tools silently clamp to `MAX_SAMPLE_COUNT=50` so a request
for `1000` returns 50 with the actual `SampleResult.count` field
reflecting the truth.

---

## "DDS topic name is malformed"

**What it means.** You passed a topic string that doesn't match the
relaxed DDS regex `^[A-Za-z_/][A-Za-z0-9_/:]*$`. This validator
accepts the OMG-DDS conventions (no leading `/` required, `::`
separators allowed, builtin DCPS names like `DCPSParticipant`) but
still rejects whitespace, shell metacharacters, and dashes.

**How to fix.** Strip dashes / spaces from the topic name. ROS2 topic
names always start with `/` and use `_` (never `-`) for word
separation, so the typical fix is `my-topic` -> `my_topic` or
`/my_topic`.

---

## "ROS2 CLI not found on PATH" / mode falls back to `mock`

**Symptom.** `health_check` reports `mode_effective: "mock"` even
though you set `TOPICFORGE_MODE=live` or `auto`.

**Diagnostics.**

1. From the **same shell** that spawned TopicForge (this is critical
   for desktop MCP clients: PATH and venv activation are not
   inherited across GUI launchers) :
   ```bash
   which ros2          # Linux / WSL: should print /opt/ros/<distro>/bin/ros2
   where.exe ros2      # Windows: should print a .cmd / .bat path
   ```
2. If `ros2` is not on PATH, source the ROS2 setup file in the parent
   shell **before** launching the MCP client.
3. Override the binary explicitly :
   ```bash
   TOPICFORGE_ROS2_BIN=/opt/ros/humble/bin/ros2 python -m topicforge
   ```

On Windows native, `ros2.cmd` is what `shutil.which` resolves:
TopicForge handles the shell-shim resolution. Make sure the install
directory containing `ros2.cmd` is on `%PATH%`.

---

## Where to look next

- [README.md](../README.md): install, run, configure
- [docs/DDS_QUICKSTART.md](DDS_QUICKSTART.md): 5-minute DDS walkthrough
- [docs/TESTING.md](TESTING.md): five-path setup guide
- [docs/MIGRATION_v0.3_to_v0.4.md](MIGRATION_v0.3_to_v0.4.md): most recent migration
- [docs/product-plan.md](product-plan.md): strategic roadmap (Phase 3 hosted endpoint reopens many security caveats)

Open issues : https://github.com/yaniswav/TopicForge/issues.
