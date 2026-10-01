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

If you genuinely want a DDS-only deployment (no ROS2), this message is
expected and harmless: since 0.5.3 an explicit `TOPICFORGE_DDS_BACKEND`
(`cyclone`, `fast` or `auto`) gives you the DDS adapter alone, even in
`auto` mode without `ros2`, and you only call the five DDS tools
(`list_participants`, `detect_qos_mismatches`, `peek_dds_samples`,
`participant_events`, `topic_metrics`). The two bag tools,
`analyze_bag` and `peek_bag_samples`, are served by the ROS2 half and
raise this error in a DDS-only process. For offline development use
`TOPICFORGE_MODE=mock`.

---

## "rosbags" / `_ROSBAGS_REQUIRED_MSG`

**Full message** (paraphrased):

> Bag analysis with full sample decode requires the `rosbags` library.
> Install via `pip install topicforge[bags]` and retry. `analyze_bag`
> may still fall back to the v0.3.0 `ros2 bag info` text-parse path via
> the live ROS2 CLI adapter ; `peek_bag_samples` has no fallback.

**What it means.** You called `peek_bag_samples` without the optional
`rosbags` Apache-2.0 library installed. The library is intentionally
optional: base `pip install topicforge` keeps the install footprint
small. `analyze_bag` does not raise this error: in live mode it runs
`ros2 bag info` and never touches `rosbags`, whether or not the
library is installed. The message text above mentions an
`analyze_bag` fallback for historical reasons; there is no `rosbags`
path in `analyze_bag` to fall back from.

`peek_bag_samples` is served only by the ROS2 CLI adapter (alone or as
the ROS half of a composite) and by the mock adapter. With a DDS-only
adapter (explicit DDS backend, no `ros2` on PATH) it raises the
"DDS observability only" error instead. Without `ros2` and without a
DDS backend, the server runs on the mock adapter and returns mock
fixture samples, not the content of your file: check `health_check`
(`mode: "mock"`) before trusting bag output.

**How to fix.**

```bash
pip install topicforge[bags]
```

This pulls `rosbags>=0.9` (pure-Python, no native deps, works on all
supported Python/OS combos). Re-run the tool: no env-var change
needed.

If you cannot install rosbags (sandboxed CI, restricted package
allowlist, etc.), `analyze_bag` keeps working through `ros2 bag info`
text parsing in `Ros2CliAdapter`; the enriched fields (`bag_format`,
`recording_duration_ns`, ...) stay at their safe defaults.
`peek_bag_samples` is rosbags-only.

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
between the Python `fastdds` binding and the native `libfastdds`
shared library on the host.

**Ignore the "pin and reinstall" part of the message.** The `fastdds`
Python binding is not published on PyPI, and TopicForge no longer
declares it in any extra (0.5.3 removed `[dds-fast]`). Do not
`pip install fastdds`: there is no official package under that name,
and installing whatever a registry returns for it is exactly the
dependency-confusion risk the extra's removal closed.

**How to fix.**

1. Rebuild eProsima's Python binding
   ([eProsima/Fast-DDS-python](https://github.com/eProsima/Fast-DDS-python))
   against the same Fast DDS native libraries that are installed on the
   host, then install it into the environment that runs TopicForge.
2. If you set `FastDDS_DEFAULT_PROFILES_FILE`, unset it and retry: a
   broken profile XML triggers the same symptom.
3. If more than one Fast DDS native install is present (CMake / vcpkg /
   apt), make sure `LD_LIBRARY_PATH` (or `PATH` on Windows) points at
   the one the binding was built against.

The adapter was written against the 2.6.x line of the Python binding
(it uses `fastdds.StatusMask`, `DomainParticipantQos` and a
duck-typed listener). It has not been run against a live bus, and no
other version has been tried. If the binding cannot be imported at all
the server does not fail: it logs a warning and falls back to the
ROS2 CLI alone, or to the mock fixtures.

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

**Symptom.** `health_check` reports `mode: "mock"` (with
`requested_mode: "live"` or `"auto"`) even though you set
`TOPICFORGE_MODE=live` or `auto`. Since 0.5.3 `mode` describes the
adapter that was actually built, so this combination is the signal
that the server fell back to the mock fixtures. The server log carries
the matching warning. Before 0.5.3, `health_check` echoed the
configured mode and could say `live` while serving fixtures.

**Diagnostics.**

1. From the **same shell** that spawned TopicForge (this is critical
   for desktop MCP clients: PATH and venv activation are not
   inherited across GUI launchers) :
   ```bash
   which ros2          # Linux / WSL: should print /opt/ros/<distro>/bin/ros2
   where.exe ros2      # Windows: should print the ros2 launcher (typically ros2.exe)
   ```
2. If `ros2` is not on PATH, source the ROS2 setup file in the parent
   shell **before** launching the MCP client.
3. Override the binary explicitly :
   ```bash
   TOPICFORGE_ROS2_BIN=/opt/ros/humble/bin/ros2 python -m topicforge
   ```

On Windows native, TopicForge resolves the executable with
`shutil.which` and runs it by absolute path, never through a shell.
Make sure the directory containing the ROS2 launcher is on `%PATH%`
in the environment that spawns the server.

If `ros2` is missing but you have selected a DDS backend explicitly
(`TOPICFORGE_DDS_BACKEND=cyclone` or `fast`), the server does not
fall back to mock: it serves the DDS tools through a DDS-only adapter
and the ROS2 graph tools raise the "DDS observability only" error
described at the top of this page.

---

## "topicforge: configuration error: ..." at startup

**Symptom.** The server prints one line to stderr beginning with
`topicforge: configuration error:` and exits with code 2, so your MCP
client reports that the server failed to start.

**What it means.** One of the `TOPICFORGE_*` variables holds a value
the settings loader rejects. It is deliberately strict, so a typo
cannot silently change behaviour. The three you are most likely to
meet:

- `Invalid TOPICFORGE_TELEMETRY=...`: only `on`, `1`, `true`, `yes`,
  `enabled` turn telemetry on, and only unset/empty, `off`, `0`,
  `false`, `no`, `disabled` turn it off. Before 0.5.3 any other value
  was treated as off; now it stops the server.
- `TOPICFORGE_DDS_BACKEND='rti' was removed in 0.5.3 ...` (also
  `opensplice`, `coredx`, `intercom`): these were values for the
  retired Pro tier. Use `cyclone`, `fast` or `auto`. A Cyclone
  participant still discovers RTI, CoreDX and OpenSplice participants
  through standard RTPS discovery.
- `Invalid TOPICFORGE_MODE`, `TOPICFORGE_LOG_LEVEL`,
  `TOPICFORGE_DDS_DOMAIN_ID` (an integer in `0..232`).

**How to fix.** Correct the variable in the `env` block of your MCP
client configuration (or in the shell that spawns the server) and
restart the client. Desktop clients show the stderr line in their MCP
log.

---

## Where to look next

- [README.md](../README.md): install, run, configure
- [docs/DDS_QUICKSTART.md](DDS_QUICKSTART.md): 5-minute DDS walkthrough
- [docs/TESTING.md](TESTING.md): five-path setup guide
- [docs/MIGRATION_v0.3_to_v0.4.md](MIGRATION_v0.3_to_v0.4.md): most recent migration
- [docs/product-plan.md](product-plan.md): strategic roadmap (Phase 3 hosted endpoint reopens many security caveats)

Open issues : https://github.com/yaniswav/TopicForge/issues.
