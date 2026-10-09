# Using TopicForge from your MCP client

Last checked 2026-10-07.

TopicForge (ROS 2 / DDS) is a local, read-only MCP server that speaks stdio.
Every client below launches it as a child process with the same command:

```
uvx --from "topicforge==0.6.4" topicforge
```

and the same three environment variables:

| Variable | Value | Meaning |
| --- | --- | --- |
| `TOPICFORGE_MODE` | `auto` | Use ROS 2 if it is on PATH, else fixtures. |
| `TOPICFORGE_DDS_BACKEND` | `cyclone` | Join the DDS bus as a read-only participant (the `cyclonedds` binding is installed with TopicForge on supported platforms). Use `mock` for a demo with no robot. |
| `TOPICFORGE_DDS_DOMAIN_ID` | `0` | The one DDS domain to observe (0-232). Fixed at startup: restart the server to change it. |

The registry name is `io.github.yaniswav/topicforge`. Where a client asks for a
display name, use **TopicForge (ROS 2 / DDS)**: an unrelated SEO product is also
called TopicForge in the MCP registries.

The version pin (`==0.6.4`) is moved on every release together with the other
version strings; a test keeps them in sync.

## Before you start

- **uv.** `uvx` ships with [uv](https://docs.astral.sh/uv/getting-started/installation/).
  Install it once, then check `uvx --version` in a new terminal.
- **Desktop apps do not inherit your shell PATH.** On Windows especially, an app
  started from the Start menu may not see `uvx`. If a client reports "command not
  found", put the absolute path in `command` (find it with `where uvx` on Windows
  or `which uvx` elsewhere, for example `C:/Users/you/.local/bin/uvx.exe`;
  forward slashes are fine in JSON).
- **First start is slow.** `uvx` downloads the package and the Cyclone DDS binding
  the first time. Clients with a short startup timeout may need a retry or a
  larger timeout (noted per client below).
- **Platforms without a Cyclone wheel.** Since 0.6.3 `cyclonedds` is a normal dependency
  of `topicforge`, installed only where it ships prebuilt wheels: Python 3.10 to 3.13 on
  Linux x86_64, Windows x64 and macOS. On Linux ARM (Jetson, Raspberry Pi), Windows ARM64
  or Python 3.14 the server still starts and `health_check` says why the DDS backend is
  off (`dds_inactive_note`); with a pinned Python, add `"--python", "3.12"` before
  `"--from"` in the args. To build the binding yourself, install the Cyclone C library and
  run `pip install "topicforge[dds]"`. The `[dds]` and `[dds-cyclone]` extras still work
  as aliases, so older commands keep installing.
- **pip alternative.** `pip install "topicforge==0.6.4"`, then use
  `"command": "topicforge"` (or `"command": "python", "args": ["-m", "topicforge"]`)
  with no `args` for uvx. Use the absolute path of the binary if the client does
  not see your venv.
- **No robot at hand?** Set `TOPICFORGE_DDS_BACKEND` to `mock` to try the tools
  against deterministic fixtures.

## What cannot work: ChatGPT and hosted endpoints

ChatGPT on the web and in the desktop app cannot run local stdio MCP servers; it
only talks to remote MCP endpoints. Use Codex (below), which runs local stdio
servers, or any other client on this page.

A hosted TopicForge endpoint is out of scope on purpose. TopicForge has to sit on
the same network as the DDS participants it observes, and exposing a robot bus to
the internet would contradict the read-only safety promise the product is built
on.

## The common JSON shape

Most clients read an `mcpServers` object like this one. The per-client sections
say where the file lives and what differs.

```json
{
  "mcpServers": {
    "topicforge": {
      "command": "uvx",
      "args": ["--from", "topicforge==0.6.4", "topicforge"],
      "env": {
        "TOPICFORGE_MODE": "auto",
        "TOPICFORGE_DDS_BACKEND": "cyclone",
        "TOPICFORGE_DDS_DOMAIN_ID": "0"
      }
    }
  }
}
```

## Claude Code

Plugin (server plus two skills, asks for backend and domain at install):

```
claude plugin marketplace add yaniswav/TopicForge
claude plugin install topicforge@topicforge
```

Or just the server:

```
claude mcp add topicforge --env TOPICFORGE_MODE=auto --env TOPICFORGE_DDS_BACKEND=cyclone --env TOPICFORGE_DDS_DOMAIN_ID=0 -- uvx --from "topicforge==0.6.4" topicforge
```

## Claude Desktop

From 0.6.3: download `topicforge-<version>.mcpb` from the latest GitHub release and double-click it. Claude Desktop asks for the DDS backend and domain id and manages Python and dependencies itself through uv.

Or edit the config file by hand: `claude_desktop_config.json` (Settings > Developer > Edit Config):
`%APPDATA%\Claude\claude_desktop_config.json` on Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS. Paste
the common JSON shape above and restart the app completely. On Windows, if
`uvx` is not found, use its absolute path as `command`.

## Cursor

The one-click links in this page and in the README are https redirects, because GitHub and PyPI strip `cursor://` and `vscode:` links. Only the raw schemes are documented by Cursor and VS Code; the https forms (`cursor.com/install-mcp`, `vscode.dev/redirect/mcp/install`) were observed to redirect correctly on 2026-10-06 but are not in the vendors' docs (unverified, last checked 2026-10-06). The raw links are given as a fallback.

One click: [Add TopicForge to Cursor](https://cursor.com/install-mcp?name=topicforge&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJ0b3BpY2ZvcmdlPT0wLjYuNCIsInRvcGljZm9yZ2UiXSwiZW52Ijp7IlRPUElDRk9SR0VfTU9ERSI6ImF1dG8iLCJUT1BJQ0ZPUkdFX0REU19CQUNLRU5EIjoiY3ljbG9uZSIsIlRPUElDRk9SR0VfRERTX0RPTUFJTl9JRCI6IjAifX0%3D)

If that page does not open Cursor, use the raw deeplink: `cursor://anysphere.cursor-deeplink/mcp/install?name=topicforge&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJ0b3BpY2ZvcmdlPT0wLjYuNCIsInRvcGljZm9yZ2UiXSwiZW52Ijp7IlRPUElDRk9SR0VfTU9ERSI6ImF1dG8iLCJUT1BJQ0ZPUkdFX0REU19CQUNLRU5EIjoiY3ljbG9uZSIsIlRPUElDRk9SR0VfRERTX0RPTUFJTl9JRCI6IjAifX0=`

Or paste the common JSON shape into `~/.cursor/mcp.json` (all projects) or
`.cursor/mcp.json` (one project).

## Agent Plugins (Cursor and other compatible clients)

The `plugin/` folder of this repository is also an [Agent Plugins](https://agent-plugins.org) 1.0.0 package: `plugin.json` (manifest), `mcp.json` (the same pinned `uvx` launch) and `skills/` (two skills). Clients that load that format, such as Cursor, can install it as a plugin and get the server and the skills in one step; the plain JSON config above remains the simplest route for everything else. The format has no user-configuration prompt, so the backend is fixed to `cyclone` and the domain to `0`; edit the `env` block of `mcp.json` to change them (see `plugin/README.md`). Cursor Directory (cursor.directory/plugins/new) and the Cursor marketplace take a repository URL; a repo-root `.cursor-plugin/marketplace.json` points Cursor at the `plugin/` subfolder. Whether each directory scanner accepts a subfolder is unverified.

## VS Code / GitHub Copilot

One click: [Install in VS Code](https://vscode.dev/redirect/mcp/install?name=topicforge&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22topicforge%3D%3D0.6.4%22%2C%22topicforge%22%5D%2C%22env%22%3A%7B%22TOPICFORGE_MODE%22%3A%22auto%22%2C%22TOPICFORGE_DDS_BACKEND%22%3A%22cyclone%22%2C%22TOPICFORGE_DDS_DOMAIN_ID%22%3A%220%22%7D%7D)

Fallback, the raw URL handler: `vscode:mcp/install?%7B%22name%22%3A%22topicforge%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22topicforge%3D%3D0.6.4%22%2C%22topicforge%22%5D%2C%22env%22%3A%7B%22TOPICFORGE_MODE%22%3A%22auto%22%2C%22TOPICFORGE_DDS_BACKEND%22%3A%22cyclone%22%2C%22TOPICFORGE_DDS_DOMAIN_ID%22%3A%220%22%7D%7D`

Or put this in `.vscode/mcp.json` (the key is `servers`, not `mcpServers`), or run
"MCP: Add Server" from the Command Palette:

```json
{
  "servers": {
    "topicforge": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "topicforge==0.6.4", "topicforge"],
      "env": {
        "TOPICFORGE_MODE": "auto",
        "TOPICFORGE_DDS_BACKEND": "cyclone",
        "TOPICFORGE_DDS_DOMAIN_ID": "0"
      }
    }
  }
}
```

## Windsurf / Devin (Cascade)

Windsurf's docs now redirect to Devin's (unverified, last checked 2026-10-06). Edit `%APPDATA%\devin\mcp_config.json`
(Windows) or `~/.config/devin/mcp_config.json` (macOS, Linux) and paste the common
JSON shape. Older installs may still use `~/.codeium/windsurf/mcp_config.json` (unverified, last checked 2026-10-06).

## Cline

Open the MCP Servers icon in the Cline panel, then Configure, then "Configure MCP
Servers", and paste the common JSON shape (the file is
`~/.cline/data/settings/cline_mcp_settings.json` per docs.cline.bot on 2026-10-06; older VS Code installs kept it elsewhere, unverified). Optional fields such as
`"disabled": false` and `"autoApprove": []` are accepted.

## Roo Code

Project file `.roo/mcp.json` (takes precedence) or the global `mcp_settings.json`
opened from the MCP view. Paste the common JSON shape. Roo's docs say Windows
needs a `cmd /c` wrapper for `npx`; for `uvx`, try it directly first and fall back
to `"command": "cmd", "args": ["/c", "uvx", "--from", "topicforge==0.6.4",
"topicforge"]` if it fails to start.

## Continue

Create `.continue/mcpServers/topicforge.yaml` (MCP works in agent mode only):

```yaml
name: TopicForge (ROS 2 / DDS)
version: 0.0.1
schema: v1
mcpServers:
  - name: topicforge
    type: stdio
    command: uvx
    args:
      - "--from"
      - "topicforge==0.6.4"
      - "topicforge"
    env:
      TOPICFORGE_MODE: auto
      TOPICFORGE_DDS_BACKEND: cyclone
      TOPICFORGE_DDS_DOMAIN_ID: "0"
```

## Zed

In `settings.json`, under `context_servers`:

```json
{
  "context_servers": {
    "topicforge": {
      "command": "uvx",
      "args": ["--from", "topicforge==0.6.4", "topicforge"],
      "env": {
        "TOPICFORGE_MODE": "auto",
        "TOPICFORGE_DDS_BACKEND": "cyclone",
        "TOPICFORGE_DDS_DOMAIN_ID": "0"
      }
    }
  }
}
```

## JetBrains AI Assistant

Settings > Tools > AI Assistant > Model Context Protocol (MCP) > Add, then paste the
common JSON shape. You can also use "Import from Claude" if you already set it up
there.

## OpenAI Codex CLI

Codex runs local stdio servers (CLI, IDE extension and desktop app share
`~/.codex/config.toml`). The default startup timeout is 10 s, which a first `uvx`
run with the dds extra can exceed, so set it to 60 s:

```toml
[mcp_servers.topicforge]
command = "uvx"
args = ["--from", "topicforge==0.6.4", "topicforge"]
startup_timeout_sec = 60

[mcp_servers.topicforge.env]
TOPICFORGE_MODE = "auto"
TOPICFORGE_DDS_BACKEND = "cyclone"
TOPICFORGE_DDS_DOMAIN_ID = "0"
```

## Gemini CLI

As an extension (reads `gemini-extension.json` from this repository):

```
gemini extensions install https://github.com/yaniswav/TopicForge
```

Or paste the common JSON shape into `~/.gemini/settings.json` (or
`.gemini/settings.json` in a project). Add `"timeout": 60000` (milliseconds) for
the slow first start, and do not set `"trust": true`.

## Goose

In `~/.config/goose/config.yaml` (macOS, Linux; the Windows location is unverified, last checked 2026-10-06), or via the extensions screen:

```yaml
extensions:
  topicforge:
    name: TopicForge (ROS 2 / DDS)
    type: stdio
    cmd: uvx
    args: [--from, "topicforge==0.6.4", topicforge]
    enabled: true
    envs: { "TOPICFORGE_MODE": "auto", "TOPICFORGE_DDS_BACKEND": "cyclone", "TOPICFORGE_DDS_DOMAIN_ID": "0" }
    timeout: 300
```

## LM Studio

Program tab > Install > Edit `mcp.json` (same notation as Cursor), then paste the
common JSON shape. LM Studio's docs only show remote examples; local command
servers follow the Cursor notation but are not documented explicitly. Small
local models may struggle with fourteen tools.

## Amazon Q Developer / Kiro

Kiro: `~/.kiro/settings/mcp.json` (user) or `.kiro/settings/mcp.json` (workspace,
takes precedence); paste the common JSON shape. Amazon Q Developer CLI uses the
same shape in `~/.aws/amazonq/mcp.json` (the Amazon Q path and the move to Kiro CLI are unverified, last checked 2026-10-06).

## Warp

Settings > MCP > "+ Add", then paste the common JSON shape (unverified, last checked 2026-10-06: the Warp docs page could not be fetched).

## Local HTTP transport

stdio is the default and the right choice almost everywhere. When the client cannot launch
the server itself, TopicForge can serve Streamable HTTP on the local machine:

```
topicforge --transport streamable-http --port 8765
```

The endpoint is `http://127.0.0.1:8765/mcp`. Two cases it is meant for:

- WSL2 to Windows: run TopicForge inside WSL2 next to ROS 2, point a Windows client at
  `http://127.0.0.1:8765/mcp` (WSL2 forwards localhost to Windows).
- A robot: run it on the robot, then `ssh -L 8765:127.0.0.1:8765 robot` and use the same URL
  on your laptop.

Threat model: the server has no authentication, so anything that can reach the port can read
your robot graph. It therefore binds `127.0.0.1` only (there is no flag to change that) and
rejects any request whose `Host` or `Origin` is not a loopback name with the served port, which
stops a web page in your browser from reaching it through DNS rebinding. Do not forward the
port to a network interface; use an SSH tunnel instead.

## Prompts and instructions

The server offers two MCP prompts, `diagnose-dds-bus` (nodes do not talk, a topic gets no data,
a node crashed) and `inspect-ros2-robot` (what topics exist, what a bag contains), plus short
server `instructions` sent in the `initialize` result (the read-only guarantee, which tool to
call first, `contract_version`). Both prompts take optional arguments (`topic`, `symptom` or
`bag_path`) that only focus the text; they run no tool by themselves.

| Client | Prompts | Instructions |
| --- | --- | --- |
| Claude Code, Claude Desktop | yes (slash commands / prompt picker) | yes |
| Cursor | yes | yes |
| VS Code / GitHub Copilot | yes (`/mcp.topicforge.<prompt>` in chat) | yes |
| Gemini CLI | yes (each prompt becomes a slash command) | yes |
| OpenAI Codex CLI | no | yes: this is how Codex learns the call order |
| Other clients | depends on the client; check its MCP page | most read them |

The Claude plugin in `plugin/` ships the same procedures as skills, which load by themselves
when the question matches.

## Troubleshooting

- Run the command by hand first: `uvx --from "topicforge==0.6.4" topicforge --version`.
- Server starts but sees no DDS participants: check the domain id, and that the
  client machine is on the same network as the robot (DDS discovery uses
  multicast UDP). Ask the assistant to call `health_check`.
- More in [TROUBLESHOOTING.md](TROUBLESHOOTING.md) and [TESTING.md](TESTING.md).
