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
- `analyze_bag` and `peek_bag_samples` open whatever path the MCP
  client passes (no workspace isolation, no symlink restriction). Since
  0.10 this holds without `ros2` too: when the server falls back to the
  mock adapter, or has a DDS-only backend, any existing path is read
  with `rosbags`; only `TOPICFORGE_MODE=mock` never touches the file
  system. A missing path is an error whose text echoes the path. The threat model
  assumes the client is your trusted agent acting on your behalf.
  The same holds for `get_node_info`'s `params_file` (a `.yaml` or
  `.yml` file on the server, at most 1 MiB, read with
  `yaml.safe_load`, secret-looking values masked) and for
  `inspect_tf`'s `urdf_path` (a `.urdf` file on the server, at most
  1 MiB, parsed without DOCTYPE or ENTITY, never run through xacro): over the HTTP
  transport, a client can learn whether such a file exists at a
  path it names, so expose that transport only to clients you trust.
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

**Architecture.** No tool, adapter method or TopicForge code publishes a
message to a topic, calls a service other than the parameter read below, or
sets a parameter. What does go out is what ROS 2 itself does on creating a node
in a process TopicForge starts: the table below lists those writers, and the
helper's node sends one event about its own parameters at start. The ros2 adapter only runs `ros2 topic list`,
`topic info`, `topic echo`, `service list`, `bag info`, `node list`,
`node info` and `param dump`; it never runs `topic pub`, `service call`, `param set`,
`action send_goal` or `bag record`. `sample_messages` may instead start the
sampler helper (`adapters/ros2_live/_sampler_helper.py`), which subscribes to
one topic and asks for nothing else; `tests/test_sampler_frames.py` fails if
that script mentions `.publish(`, `create_publisher`, `create_service` or
`create_client`.
The Cyclone adapter creates one `DomainParticipant` plus builtin discovery
readers (`BuiltinDataReader`); no `DataWriter` or `Publisher` exists in
the DDS adapters or in `adapters/common`. With `TOPICFORGE_DDS_DECODE=on`
each decoded read also creates one short-lived participant with one reader,
and no writer (`adapters/dds_cyclone/user_read.py`).

**Entities, per process TopicForge starts.** A ROS 2 process cannot exist
without some writers, even one that never publishes a message of its own, so
"no writer" holds for the Cyclone participant only.

| Process | Lives for | Readers | Writers |
| --- | --- | --- | --- |
| The server's Cyclone participant (`topicforge`) | The server | Builtin discovery readers | None |
| `ros2 topic echo` and other `ros2` commands | One call | The sampled topic (`echo`), `ros_discovery_info`, and on Jazzy the request of the type-description service | `rt/rosout`, `rt/parameter_events`, `ros_discovery_info`, and on Jazzy the reply of the type-description service |
| Decoded read (`topicforge_reader`, `TOPICFORGE_DDS_DECODE=on`) | One read | The read topic, builtin discovery readers | None |
| Sampler helper (`_topicforge_sampler_<pid>`) | One call | The sampled topic, `ros_discovery_info` | `rt/parameter_events`, with one event about its own parameters at start, and `ros_discovery_info` |

The helper starts with `enable_rosout=False`, `start_parameter_services=False`
and, from Iron on, `start_type_description_service=False`. The bench checks its
exact writer set on a live bus (`compare.py`, section `sampler`).

**The single exception (from 0.7.0, with `get_node_info`).** The only request TopicForge ever sends is a parameter read (list/get). It sends no other request to a node: no other service call, no parameter set, no lifecycle transition, no action goal.

**Declaration.** All eighteen tools carry MCP `ToolAnnotations`:
`readOnlyHint=true`, `destructiveHint=false`, `idempotentHint=true`, a
`title`, and an honest `openWorldHint` (true for tools that observe a
live ROS 2 graph or DDS bus, false for `health_check`, `analyze_bag` and
`peek_bag_samples`, which read only the local environment or local
files). They are built in one helper, `read_only_annotations` in
`src/topicforge/tools/annotations.py`, so no tool can be registered
without the read-only base. Clients may use these hints, for example
to skip confirmation prompts. They are hints; the guarantee is the
architecture.

**Proof.** `tests/test_tool_annotations.py` builds the app in mock mode,
lists the tools through the MCP layer as a client would, and fails if the
tool count is not eighteen, if any tool lacks annotations, is not read-only,
non-destructive and idempotent, has no title or a non-boolean
`openWorldHint`, or if the set of closed-world tools differs from the
three above. Adding an eighteenth tool without annotations fails the suite.

**What read-only does not mean: not perfectly passive.**

- Joining a DDS domain is observable. The Cyclone participant (named
  `topicforge`) announces itself through standard SPDP/SEDP discovery, so
  other participants see it, and it creates builtin discovery readers
  (DCPSParticipant, DCPSPublication, DCPSSubscription). It never creates a
  writer. By default it creates no reader on user topics.
- With `TOPICFORGE_DDS_DECODE=on` (off by default), `peek_dds_samples` and
  `topic_metrics` on a ROS 2 topic create a reader on it for the bounded
  read, on a separate participant named `topicforge_reader`. **Decoding adds
  a visible subscriber, and a publisher that only works when someone listens
  may wake.** A Fast DDS writer can count that reader for up to about 10 s
  after the read (measured, the participant's lease); a Cyclone writer for
  about 1 s. Measured on a real bus by an independent observer: these
  participants announce no writer; like any DDS participant they have the
  builtin discovery endpoints the protocol requires.
- The ros2 CLI path starts `ros2` processes, which can start the ros2
  daemon if it is not already running. `sample_messages` runs
  `ros2 topic echo` or the sampler helper, which creates a temporary
  subscription (a reader) on the topic, visible to the graph for the
  duration of the sample.

**Environment read.** Only `TOPICFORGE_*` variables (mode, log level,
`TOPICFORGE_ROS2_BIN`, DDS backend, domain and decoding, telemetry) and `ROS_DISTRO`
for reporting. The `ros2` child processes inherit the environment
(`ROS_DOMAIN_ID`, `RMW_IMPLEMENTATION` and the like), as any ROS 2 tool
would. Telemetry is off by default and a verified no-op when off
(pinned by `tests/test_telemetry.py::test_build_app_off_makes_no_transport_calls`).

**Remote access.** TopicForge has no rosbridge client and no ssh targets,
by design: it never reaches out to a robot. Run it on the robot (or a
machine on its network) and reach it through a local transport or a
tunnel you control. A loopback streamable-http
transport ships in 0.7.0 (127.0.0.1, no authentication); reach a remote
robot through an SSH tunnel (see the "Local HTTP transport" section of
docs/CLIENTS.md).

**HTTP bearer token (0.8.1).** `TOPICFORGE_HTTP_TOKEN` (at least 32
printable characters, read from the environment only) makes every HTTP
request carry `Authorization: Bearer <token>`, compared in constant time;
anything else gets `401`. Only with a token may `--host` bind an address
other than loopback, and for such a bind the DNS-rebinding allow-list is
lifted, since the token, not the `Host` header, is what keeps a foreign
page out. What the token does not do: the transport is plain HTTP, so the
token and every answer cross the network in clear and anyone who can read
that traffic can replay the token; there is no rate limit on wrong tokens
(32 characters put guessing out of reach) and no per-client identity or
audit. Use it on a network you trust or behind a TLS reverse proxy; an SSH
tunnel remains the recommended way to reach a robot.

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

Only the latest release on PyPI receives security patches. Releases 0.3.0 to 0.5.2 are yanked from PyPI.

## Disclosure

For confirmed vulnerabilities, I publish a GitHub advisory after the
patch ships, naming the reporter (with permission) and including a
CVE if the impact warrants it.
