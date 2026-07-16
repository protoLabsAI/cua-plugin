"""Resolve the cua-driver binary and build the managed MCP server entry.

The driver is a third-party binary (trycua/cua, MIT) that we spawn but never
install: the factory probes for it and returns ``None`` when it's missing, which
is ``register_mcp_server``'s documented "don't start" path. That keeps the
install — and the macOS TCC grants it needs — a deliberate human action.

Host-only imports stay lazy so the tests run with no protoAgent host.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("protoagent.plugins.cua")

#: Name of the MCP server entry. Driver tools reach the agent as ``cua-driver__<tool>``.
SERVER_NAME = "cua-driver"

_BINARY = "cua-driver"

#: Probed in order when ``binary_path`` is blank and PATH misses. ``~/.local/bin``
#: is the upstream installer's default target (``BIN_DIR="${CUA_DRIVER_BIN_DIR:-$HOME/.local/bin}"``),
#: and it is routinely absent from a service's PATH — notably the frozen desktop
#: sidecar, which does not inherit your shell's environment.
_FALLBACK_DIRS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")

#: The documented snapshot->act loop (ADR 0084 D3). The driver exposes ~28 tools;
#: binding all of them costs context on every turn for surface the agent rarely
#: needs (ADR 0005). Deliberately excluded from the default, available via
#: ``extra_tools``:
#:   bring_to_front     — steals focus, which is the whole thing the driver avoids
#:   kill_app           — destructive
#:   get_accessibility_tree — raw tree; get_window_state is the curated view
#:   get_config/set_config, health_report, cursor tools — driver-tuning surface
#:   page/browser tools — a second capability with its own protocol (WEB_APPS.md)
#:   check_for_update   — driver self-update; ADR 0084 keeps that operator-driven
#:
#: These names are inferred from the driver's tool modules and are NOT verified at
#: import time. ``include`` silently drops names the server doesn't publish, so a
#: wrong name is a missing tool, not a crash — ``probe`` diffs this list against
#: the driver's real surface and the Test button reports the mismatch.
CORE_TOOLS: tuple[str, ...] = (
    # discover
    "list_apps",
    "list_windows",
    "get_screen_size",
    "get_desktop_state",
    # lifecycle
    "launch_app",
    # snapshot — the invariant; also the capture path (capture_mode:"vision")
    "get_window_state",
    # act — pointer
    "click",
    "double_click",
    "right_click",
    "drag",
    "scroll",
    # act — keyboard / values
    "type_text",
    "press_key",
    "hotkey",
    "set_value",
    # aids
    "zoom",
    "check_permissions",
)


def plugin_section(config) -> dict:
    """This plugin's live config section off a ``LangGraphConfig``.

    Read from ``config.plugin_config`` rather than the register-time snapshot so
    the factory — called on every graph build — sees edits after a reload.
    """
    return dict((getattr(config, "plugin_config", None) or {}).get("cua") or {})


def resolve_binary(configured: str = "") -> str | None:
    """Absolute path to an executable cua-driver, or None.

    An explicit ``binary_path`` is authoritative: if it's set and wrong, that's a
    misconfiguration to surface, not something to paper over with a PATH probe.
    """
    if configured and configured.strip():
        p = Path(os.path.expanduser(configured.strip()))
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
        log.warning("[cua] binary_path %r is not an executable file", configured)
        return None

    found = shutil.which(_BINARY)
    if found:
        return found

    for d in _FALLBACK_DIRS:
        p = Path(os.path.expanduser(d)) / _BINARY
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def tool_filter(section: dict) -> dict | None:
    """The entry's ``tools`` filter, or None to bind the driver's whole surface.

    ``{"include": [...]}`` is core's allowlist — only those tools survive
    (graph/config.py, mcp.servers). Unknown names drop silently.
    """
    surface = str(section.get("tool_surface") or "core").strip().lower()
    if surface == "full":
        return None

    extra = [str(t).strip() for t in (section.get("extra_tools") or []) if str(t).strip()]
    # dict.fromkeys: dedupe, keep order, so the entry reads deterministically.
    return {"include": list(dict.fromkeys([*CORE_TOOLS, *extra]))}


def build_entry(section: dict) -> dict | None:
    """An ``mcp.servers[]`` entry for the driver, or None when it shouldn't start."""
    if not section.get("enabled"):
        return None

    binary = resolve_binary(str(section.get("binary_path") or ""))
    if not binary:
        log.warning(
            "[cua] enabled but the %s binary was not found (PATH, %s) — computer-use "
            "tools are unavailable. Install it (see the plugin README) or set "
            "cua.binary_path.",
            _BINARY,
            ", ".join(_FALLBACK_DIRS),
        )
        return None

    entry: dict = {
        "name": SERVER_NAME,
        "transport": "stdio",
        "command": binary,
        "args": ["mcp"],
        # Persistent session (the host default) is load-bearing here, not a perf
        # tweak: the driver caches the per-(pid, window_id) AX element map in the
        # process the session talks to, and `element_index` args resolve against
        # that cache. A stateless session spawns a fresh `cua-driver mcp` per call
        # and the cache dies between snapshot and click. Note the host computes
        # `mcp.persistent_sessions AND entry.persistent`, so a global
        # `persistent_sessions: false` still breaks element_index and this flag
        # cannot override it — `probe` can't see that, so the README calls it out.
        "persistent": True,
    }

    filt = tool_filter(section)
    if filt is not None:
        entry["tools"] = filt
    return entry


def build_mcp_factory():
    """``factory(config) -> entry | None``, called on every graph build."""

    def factory(config):
        return build_entry(plugin_section(config))

    return factory


def _run(binary: str, args: list[str], timeout: float = 10.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(  # noqa: S603 — argv list, no shell; binary is operator-configured
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"`{_BINARY} {' '.join(args)}` timed out after {timeout:g}s"
    except OSError as e:
        return 126, "", f"could not run {binary}: {e}"
    return p.returncode, p.stdout or "", p.stderr or ""


def _tool_names(stdout: str) -> list[str]:
    """Tool names out of `cua-driver list-tools`, JSON or plain text.

    The output shape isn't a stable contract, so parse defensively and fall back
    to line-scraping rather than reporting a false "no tools".
    """
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        data = None

    if isinstance(data, list):
        out = []
        for item in data:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and item.get("name"):
                out.append(str(item["name"]))
        if out:
            return out
    if isinstance(data, dict):
        tools = data.get("tools")
        if isinstance(tools, list):
            out = [t if isinstance(t, str) else str(t.get("name", "")) for t in tools]
            return [t for t in out if t]

    names = []
    for line in stdout.splitlines():
        tok = line.strip().split()[0] if line.strip() else ""
        # tool names are snake_case; subcommands are kebab-case (upstream SKILL.md)
        if tok and "-" not in tok and tok.replace("_", "").isalnum() and not tok[0].isupper():
            names.append(tok)
    return names


def probe(section: dict) -> dict:
    """Health check behind the Settings "Test connection" button.

    Answers the three things that actually go wrong, in the order they go wrong:
    is the binary there, are the macOS TCC grants in place, and do our allowlisted
    names match the tools this driver build really publishes.
    """
    binary = resolve_binary(str(section.get("binary_path") or ""))
    if not binary:
        return {
            "ok": False,
            "error": (
                f"{_BINARY} not found on PATH or in {', '.join(_FALLBACK_DIRS)}. "
                "Install it (README ▸ Setup), then set “cua-driver path” if the "
                "server runs with a different PATH than your shell."
            ),
        }

    rc, out, err = _run(binary, ["list-tools"])
    if rc != 0:
        return {"ok": False, "identity": binary, "error": (err or out or f"`{_BINARY} list-tools` exited {rc}").strip()}

    published = _tool_names(out)
    surface = str(section.get("tool_surface") or "core").strip().lower()
    filt = tool_filter(section)
    requested = list(filt["include"]) if filt else list(published)
    unknown = [t for t in requested if published and t not in published]
    bound = [t for t in requested if t in published] if published else requested

    notes = []
    if unknown:
        notes.append(
            "These configured tools don't exist in this driver build and will be ignored: " + ", ".join(unknown)
        )
    if surface == "full" and len(published) > 20:
        notes.append(f"“full” binds {len(published)} tools into every turn — “core” binds {len(CORE_TOOLS)}.")

    rc_p, out_p, err_p = _run(binary, ["check_permissions", "{}"])
    if rc_p != 0:
        notes.append(
            "Permission check failed — on macOS the driver needs Accessibility + "
            "Screen Recording grants before it can read or drive a window: "
            + (err_p or out_p or f"exit {rc_p}").strip()
        )
    elif out_p and ("false" in out_p.lower() or "denied" in out_p.lower()):
        notes.append("The driver reports a missing permission grant: " + out_p.strip()[:400])

    return {
        "ok": True,
        "identity": f"{binary} · {len(bound)} tool(s) bound" + (f" of {len(published)} published" if published else ""),
        "error": " ".join(notes) or None,
    }
