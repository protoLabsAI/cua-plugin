# cua — computer use for protoAgent

Lets the agent drive **native GUI apps on this machine**: snapshot a window's
accessibility tree, then click / type / scroll by element — without pulling the
app to the foreground while you're using the computer.

It wraps [`cua-driver`](https://github.com/trycua/cua) (MIT) as a **managed MCP
server**. The driver is a separate binary that runs out-of-process, so this
plugin adds **zero packages** to protoAgent's venv and works in the frozen
desktop build.

Design rationale, options weighed, and the evidence behind them: **protoAgent
ADR 0084**.

---

## Read this before you enable it

This is real control of your real desktop — your files, your logged-in sessions,
your accounts.

**protoAgent's safety fences do not apply to anything the agent does here.** Not
"apply weakly" — *do not apply*. The network egress allowlist lives inside the
`fetch_url` tool; the filesystem fence (ADR 0007) is a path check inside
`fs_tools`. Both are in-process checks in protoAgent's own Python tools, and a
mouse click never reaches either. An agent that can open your browser can reach
any site on the internet. An agent that can open Finder can read any file you can.

That's not a bug to be patched — it's what computer use *is*. protoAgent's
posture (ADR 0071) is trust-and-consent, not sandboxing, and this plugin is that
posture at full strength. Enable it deliberately.

If you want computer use **with** containment, that's a VM, not this plugin — see
ADR 0084 D5 on the deferred `cua-sandbox` path.

---

## Setup

**1. Install the driver.** It is not installed for you, and this plugin never
installs it — the probe just reports it missing until you do.

```sh
/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"     # macOS / Linux
irm https://cua.ai/driver/install.ps1 | iex                        # Windows
```

Lands in `~/.local/bin` by default (`--bin-dir` or `CUA_DRIVER_BIN_DIR` to
change). Uninstall: `curl -fsSL https://cua.ai/driver/uninstall.sh | bash`.

**2. Grant permissions (macOS).** The driver needs **Accessibility** and **Screen
Recording**. Don't do this by hand in System Settings — run:

```sh
cua-driver permissions grant
```

This exists because TCC grants attach to the **responsible app identity**, and
the intuitive route grants the wrong one. Approving a dialog raised from your
terminal grants *your terminal*, not the driver; the driver still can't read a
window, and every status check lies to you about it in the cheeriest way. The
`grant` subcommand launches CuaDriver via LaunchServices so the dialog attributes
to `com.trycua.driver`, then confirms.

Verify with `cua-driver permissions status --json` — it answers through the
running daemon, so a `true` carries the driver's own identity
(`"attribution": "driver-daemon"`). With no daemon it reports `unknown` rather
than guessing.

> **Don't trust `cua-driver check_permissions`.** It reports the **caller's** TCC
> identity, so from your shell it happily says `accessibility: true` on a machine
> where the driver has no grant at all. `permissions status` is the honest one.
> (This plugin's Test button uses `permissions status`; an earlier cut used the
> tool and reported a missing grant on a fully-granted machine.)

**3. Enable the plugin.** Settings ▸ Computer use ▸ *Enable computer use*, then
hit **Test connection**. That's not decorative: it's the only thing that checks
all three failure modes at once (binary found, TCC granted for the *driver*, and
configured tool names real for *this* driver build). A healthy result reads like:

```
/Users/you/.local/bin/cua-driver · 17 tool(s) bound of 38 published
```

**There is no step 4 — you don't manage a daemon.** When protoAgent spawns
`cua-driver mcp`, the driver notices it lacks grants under whatever launched it
and **auto-launches the CuaDriver daemon**, then proxies through it:

```
cua-driver-rs: mcp launched without CuaDriver.app's TCC grants; auto-launching
the daemon via `open -n -g -a CuaDriver --args serve` and proxying MCP requests
through it. Pass --no-daemon-relaunch to stay in-process.
```

That's why one `permissions grant` is enough: the grant lives on
`com.trycua.driver` and holds no matter who spawns the server — your shell,
`scripts/dev.sh`, or the packaged desktop app. Don't pass `--no-daemon-relaunch`;
it stays in-process and puts the grant burden back on protoAgent's own identity.

---

## Settings

| Field | Default | Notes |
|---|---|---|
| **Enable computer use** | off | Off ⇒ no server spawns, no tools exist. |
| **cua-driver path** | *(blank)* | Blank = probe PATH, then `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`. |
| **Tool surface** | `core` | `core` binds the documented loop (17 tools); `full` binds all 38. |
| **Extra tools** | *(none)* | Names added on top of `core`, e.g. `kill_app`. |

### Why `core` is the default

The driver publishes **38** tools (verified on 0.8.3). Binding all of them spends context on every turn
for surface the agent almost never needs (protoAgent ADR 0005). `core` is the
snapshot→act loop; everything else is one config edit away.

Left out of `core` on purpose:

- **`bring_to_front`** — steals focus, which is the one thing this driver exists
  to avoid.
- **`kill_app`** — destructive.
- **`get_accessibility_tree`** — despite the name, a *desktop*-level snapshot (apps + visible windows + bounds/z-order) that overlaps `list_apps`/`list_windows`. `get_window_state` is the per-window AX tree you actually act on.
- **`page`** — the browser capability, with its own session protocol (`WEB_APPS.md`).
- **`start_session` / `end_session`, recording, cursor styling** — MCP mints a session per connection, so you don't need these.
- **`check_for_update`** — driver self-update. Deliberately operator-driven
  (ADR 0084): `plugins.update_policy` can't see a binary updating itself.

`extra_tools` names that the driver doesn't publish are dropped **silently** by
the MCP filter — a wrong name is a missing tool, not an error. **Test connection**
lists the driver's real tools and flags any that don't match.

---

## Gotchas

**Don't turn off persistent MCP sessions.** `mcp.persistent_sessions: false`
**breaks element-indexed workflows.** The driver caches the per-`(pid, window_id)`
accessibility element map in the process your session talks to; a stateless
session spawns a fresh `cua-driver mcp` per call, so the cache is gone between
the snapshot that minted an `element_index` and the click that uses it. This
plugin sets `persistent: true` on its entry, but the host computes
`mcp.persistent_sessions AND entry.persistent` — a global `false` wins and there
is nothing the entry can do about it.

**The desktop app has a different PATH than your shell.** `~/.local/bin` is
routinely absent from a service's environment. If the driver works in dev and
vanishes in the packaged app, set **cua-driver path** to the absolute path.

**`screenshot` doesn't exist.** It was removed upstream (cua PR #1692) — capture
is `get_window_state` with `capture_mode: "vision"`. Docs and blog posts that
tell you to call `screenshot` are out of date.

**MCP doesn't need to be on.** A plugin-contributed server activates MCP by
itself; you don't need `mcp.enabled: true`.

---

## What it registers

- **A managed MCP server** (`register_mcp_server`) spawning `cua-driver mcp` over
  stdio. Tools arrive namespaced `cua-driver__*`. The factory returns `None` —
  the documented "don't start" path — when disabled or when the binary is absent.
  Verified end-to-end against driver 0.8.3: 17 tools bind, the allowlist holds
  exactly (no leakage from the other 21), and it activates MCP on its own —
  `mcp.enabled: true` is not required.
- **A skill** (`register_skill_dir`) teaching the snapshot-before-**and**-after
  invariant. This is not documentation garnish: the driver is
  accessibility-tree-first, `element_index` values are minted per snapshot and go
  stale across turns, and actions that silently no-op look identical to actions
  that worked. Without the protocol the tools look broken.
- **`POST /api/config/test-cua`** backing the Settings Test button.

No Python tools, no runtime dependencies.

## Development

```sh
uv venv --python 3.11 && uv pip install -r requirements-dev.txt ruff
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q
```

The suite runs with **no protoAgent host** — `tests/conftest.py` registers the
plugin as a synthetic package the way the real loader does, and fakes the
registry seam.

## License

MIT. Wraps [trycua/cua](https://github.com/trycua/cua) (MIT), installed
separately and not vendored here.
