# Security policy

## Threat model: local trust by design

TopicForge is **read-only by architecture**, not by configuration.
There is no write path in the protocol or in any shipped adapter ;
the MCP client can introspect a robot stack but cannot publish,
command, or modify anything. This is the load-bearing security
posture.

The current threat model is **local trust**: TopicForge runs as a
subprocess of your MCP client (Claude Desktop, Claude Code) on a
machine you control, inspecting your own ROS2 graph or your own bag
files. It is not hardened for adversarial inputs.

Consequences :

- `TOPICFORGE_ROS2_BIN` accepts an arbitrary path: if you point it
  at a malicious binary, TopicForge will execute it. Treat the
  variable the way you treat `PATH`.
- `analyze_bag` opens whatever path the MCP client passes (no
  workspace isolation, no symlink restriction). The threat model
  assumes the client is your trusted agent acting on your behalf.
- All `ros2` CLI invocations use `subprocess.run` with an argument
  list: never `shell=True`. Topic names are validated against a
  strict allowlist before being passed to the CLI.
- No outbound network calls by default. Opt-in anonymous usage
  telemetry is available behind `TOPICFORGE_TELEMETRY=on` ; when
  off (the default), the OFF code path is a verified no-op (pinned
  by `tests/test_telemetry.py::test_build_app_off_makes_no_transport_calls`).

The roadmap to harden TopicForge for hosted / multi-tenant
deployments lives in
[`docs/product-plan.md section 5`](docs/product-plan.md):
`TOPICFORGE_ROS2_BIN` allowlist, `subprocess.run` env scrub,
`analyze_bag` workspace-root sandbox, path traversal rejection.
Those land if a hosted MCP endpoint is ever built.

## Reporting a vulnerability

Please **do not open a public GitHub issue** for security reports.

Email : `ethvignot.yanis@gmail.com` with subject prefix `[TopicForge
security]`. Include :

- A short description of the issue (what fails, what could be
  exploited, who is at risk).
- A reproduction if possible: versions, env vars, minimal sequence
  of MCP tool calls. A failing pytest is ideal.
- Your preferred attribution wording for the public disclosure (or
  "anonymous" if you prefer).

Response timing :

- **Acknowledgement** within 7 days.
- **Triage** (confirmed / not-a-bug / known-limitation) within 14
  days.
- **Patch + advisory** for confirmed vulnerabilities : timing
  depends on severity. Critical issues get a patch release within 7
  days of triage ; lower-severity issues land in the next minor.

If a vulnerability is in scope of the documented threat model (e.g.
"setting `TOPICFORGE_ROS2_BIN` to a malicious binary lets it run") I
will close it as "by design" with a pointer to this document.
Genuine threat-model gaps are in-scope and welcome.

## Supported versions

Only the latest release receives security patches: `0.5.3` at the
time of writing. Releases 0.3.0 to 0.5.2 are yanked from PyPI.

## Disclosure

For confirmed vulnerabilities, I publish a GitHub advisory after the
patch ships, naming the reporter (with permission) and including a
CVE if the impact warrants it.
