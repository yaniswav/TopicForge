# Security audit — TopicForge v0.1.2

_Read-only audit on branch `audit/security`. No source files modified, no tests run._

## Strengths
- Every `ros2` invocation uses an argument list, never `shell=True`; only call site is `adapters/ros2_live/adapter.py:147`, verified by `grep` (the sole `shell=True` token in `src/` is a comment at `adapter.py:139`).
- Executable resolved with `shutil.which` before exec so Windows `.cmd`/`.bat` shims work without a shell (`adapter.py:140`), and `_run` translates `FileNotFoundError` + `subprocess.TimeoutExpired` + non-zero exits into `AdapterError` with a sanitized stderr tail (`adapter.py:154-169`).
- Topic-name regex `_TOPIC_NAME_RE` (`services/inspector.py:24`) is a strict allowlist enforced at the service layer; the 17-case parametrize in `tests/test_inspector.py:26-50` pins rejection of shell metacharacters, dash-flags, newlines, double-slashes, leading digits, and dotted segments.
- Bag-path validation (`inspector.py:80-98`) rejects empty/blank/null-byte paths and strips surrounding whitespace before delegating; existence and extension checks live in the adapter, keeping layers clean.
- Telemetry payload assembly is single-site (`telemetry/client.py:53` `TelemetryEvent.to_payload`); the dataclass is `frozen=True, slots=True` so adding a field requires editing one place, and `test_payload_contains_only_whitelisted_keys` (`tests/test_telemetry.py:205`) fences the exact six-key set.
- OFF mode is a true no-op: `instrument()` returns `lambda fn: fn` when disabled (`client.py:132-133`), pinned by `test_build_app_off_makes_no_transport_calls` (`test_telemetry.py:244`).
- Pro tier loads fail-closed: `ImportError` returns False without crash, broad `except Exception` around `topicforge_pro.register` logs server-side via `log.exception` and never propagates to the MCP client (`server/app.py:76-87`).

## Hardening opportunities
_Medium impact, non-blocking for v0.2._
- `TOPICFORGE_ROS2_BIN` is unbounded (`settings.py:72`) — any string is accepted then exec'd via `shutil.which`. README "Security model" line 239 calls this out, but consider rejecting values containing path separators when running under a hosted/multi-tenant context.
- `subprocess.run` inherits the parent process environment by default at `adapter.py:147` — a poisoned `PATH` or `ROS_DOMAIN_ID` in the parent leaks in. For local-trust threat model this is fine; document if/when TopicForge moves to a hosted endpoint.
- `analyze_bag` opens any path the client gives, no workspace root, no symlink restriction (acknowledged in README:240). For a hosted endpoint, wrap with a `--workspace-root` allowlist before live exec.
- `_validate_bag_path` strips whitespace but does not normalize via `Path.resolve()` — a relative `../../etc/passwd.mcap` passes the regex. Acceptable under local trust, worth tightening before hosted deployment.
- `stderr_tail` (`adapter.py:166`) surfaces the last non-empty stderr line to the MCP client. ROS2 stderr is usually benign, but a future adapter that runs user-supplied commands should sanitize this more aggressively.
- `health.py:29` reads `ROS_DISTRO` from the parent env and returns it verbatim in `HealthReport`. Low-sensitivity but reachable by any MCP client — fine for now; tag as "env disclosure, by design" in the schema docstring.

## Issues found
_None._

## Roadmap v0.3+ — security
- Sandbox `analyze_bag` reads under an explicit `--workspace-root` allowlist (config-driven, default = cwd).
- Add `Path.resolve()` + traversal-rejection in `_validate_bag_path` once a workspace root exists.
- Optional `TOPICFORGE_ROS2_BIN_ALLOWLIST` for hosted deployments — reject values not in the allowlist before `shutil.which`.
- Spawn `subprocess.run` with a scrubbed `env={}` (only `PATH`, `ROS_DOMAIN_ID`, `RMW_IMPLEMENTATION`) instead of inheriting the full parent environment.
- Sign-and-pin the optional `topicforge_pro` plugin entry point once it ships, so a name-squat on PyPI cannot inject code into `_try_register_pro`.
