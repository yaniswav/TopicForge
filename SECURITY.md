# Security policy

## Threat model: local trust by design

TopicForge is read-only by architecture, not by configuration.
There is no write path in the protocol or in any shipped adapter;
the MCP client can introspect a robot stack but cannot publish,
command, or modify anything.

The threat model is local trust: TopicForge runs as a
subprocess of your MCP client (any MCP client that launches local
stdio servers; see [docs/CLIENTS.md](docs/CLIENTS.md)) on a
machine you control, inspecting your own ROS2 graph or your own bag
files. It is not hardened for adversarial inputs.

Consequences:

- `TOPICFORGE_ROS2_BIN` accepts an arbitrary path: if you point it
  at a malicious binary, TopicForge will execute it. Treat the
  variable the way you treat `PATH`.
- `analyze_bag` opens whatever path the MCP client passes (no
  workspace isolation, no symlink restriction). The threat model
  assumes the client is your trusted agent acting on your behalf.
- All `ros2` CLI invocations use an argument list, never
  `shell=True`. Most use `subprocess.run` with a timeout;
  `sample_messages` streams `ros2 topic echo` through `subprocess.Popen`
  (`adapters/ros2_live/echo_stream.py`) and kills the whole process
  tree when the sample is complete or the budget runs out. Topic
  names are validated against a strict allowlist before being passed
  to the CLI.
- No outbound network calls by default. Opt-in anonymous usage
  telemetry is available behind `TOPICFORGE_TELEMETRY=on`; when
  off (the default), the code path is a verified no-op (pinned
  by `tests/test_telemetry.py::test_build_app_off_makes_no_transport_calls`).

Hardening for hosted / multi-tenant deployments is not done:
`TOPICFORGE_ROS2_BIN` allowlist, subprocess env scrub,
`analyze_bag` workspace-root sandbox, path traversal rejection.
Those land if a hosted MCP endpoint is ever built.

## Read-only guarantee: architecture, declaration, proof

**Architecture.** There is no write path in the protocol or in any
adapter. A search of `src/` for `create_publisher`, `call_service`,
`set_parameters`, `DataWriter` and `Publisher(` finds nothing; the only
`publish` matches are field names and comments (`publish_ns`, "publisher
count", log text). The ros2 adapter only runs `ros2 topic list`,
`topic info`, `topic echo` and `bag info`; it never runs `topic pub`,
`service call`, `param set`, `action send_goal` or `bag record`. The
Cyclone adapter creates one `DomainParticipant` plus builtin discovery
readers (`BuiltinDataReader`); no `DataWriter` or `Publisher` exists in
the DDS adapters or in `adapters/common`.

**Declaration.** All twelve tools carry MCP `ToolAnnotations`:
`readOnlyHint=true`, `destructiveHint=false`, `idempotentHint=true`, a
`title`, and an honest `openWorldHint` (true for tools that observe a
live ROS 2 graph or DDS bus, false for `health_check`, `analyze_bag` and
`peek_bag_samples`, which read only the local environment or local
files). They are built in one helper, `read_only_annotations` in
`src/topicforge/tools/annotations.py`, so no tool can be registered
without the read-only base. Clients can use these hints to skip
confirmation prompts. They are hints; the guarantee is the architecture.

**Proof.** `tests/test_tool_annotations.py` builds the app in mock mode,
lists the tools through the MCP layer as a client would, and fails if the
tool count is not twelve, if any tool lacks annotations, is not read-only,
non-destructive and idempotent, has no title or a non-boolean
`openWorldHint`, or if the set of closed-world tools differs from the
three above. Adding a thirteenth tool without annotations fails the suite.

**What read-only does not mean: not perfectly passive.**

- Joining a DDS domain is observable. The Cyclone participant (named
  `topicforge`) announces itself through standard SPDP/SEDP discovery, so
  other participants see it, and it creates builtin discovery readers
  (DCPSParticipant, DCPSPublication, DCPSSubscription). It never creates a
  writer, and it creates no reader on user topics: the typed user-topic
  reader code is disabled and unreachable (payload decoding is off). The
  `dds_fast` adapter has never run against a bus.
- The ros2 CLI path starts `ros2` processes, which can start the ros2
  daemon if it is not already running. `sample_messages` runs
  `ros2 topic echo`, which creates a temporary subscription (a reader) on
  the topic, visible to the graph for the duration of the sample.

**Environment read.** Only `TOPICFORGE_*` variables (mode, log level,
`TOPICFORGE_ROS2_BIN`, DDS backend and domain, telemetry) and `ROS_DISTRO`
for reporting. Telemetry is off by default and a verified no-op when off
(pinned by `tests/test_telemetry.py::test_build_app_off_makes_no_transport_calls`).

**Remote access.** TopicForge has no rosbridge client and no ssh targets,
by design: it never reaches out to a robot. Run it on the robot (or a
machine on its network) and reach it through a local transport or a
tunnel you control. An HTTP transport is planned, not available.

## Reporting a vulnerability

Please do not open a public GitHub issue for security reports.

Email: `ethvignot.yanis@gmail.com` with subject prefix `[TopicForge
security]`. Include:

- A short description of the issue (what fails, what could be
  exploited, who is at risk).
- A reproduction if possible: versions, env vars, minimal sequence
  of MCP tool calls. A failing pytest is ideal.
- Your preferred attribution wording for the public disclosure (or
  "anonymous" if you prefer).

Response timing:

- Acknowledgement within 7 days.
- Triage (confirmed / not-a-bug / known-limitation) within 14
  days.
- Patch and advisory for confirmed vulnerabilities: timing
  depends on severity. Critical issues get a patch release within 7
  days of triage ; lower-severity issues land in the next minor.

If a vulnerability is in scope of the documented threat model (e.g.
"setting `TOPICFORGE_ROS2_BIN` to a malicious binary lets it run") I
will close it as "by design" with a pointer to this document.
Genuine threat-model gaps are in-scope and welcome.

## Supported versions

Only the latest release receives security patches: `0.5.5` at the
time of writing. Releases 0.3.0 to 0.5.2 are yanked from PyPI.

## Disclosure

For confirmed vulnerabilities, I publish a GitHub advisory after the
patch ships, naming the reporter (with permission) and including a
CVE if the impact warrants it.
