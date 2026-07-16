"""Unit tests for binary resolution, the MCP entry, and the health probe."""

from __future__ import annotations

import stat

from cua_plugin import driver


def _fake_binary(path) -> str:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class TestResolveBinary:
    def test_explicit_path_wins(self, tmp_path):
        b = _fake_binary(tmp_path / "cua-driver")
        assert driver.resolve_binary(b) == b

    def test_explicit_path_expands_user(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        b = _fake_binary(tmp_path / "cua-driver")
        assert driver.resolve_binary("~/cua-driver") == b

    def test_explicit_path_that_is_wrong_does_not_fall_back(self, tmp_path, monkeypatch):
        """A set-but-wrong binary_path is a misconfiguration to surface, not to paper over."""
        on_path = _fake_binary(tmp_path / "cua-driver")
        monkeypatch.setattr(driver.shutil, "which", lambda _: on_path)
        assert driver.resolve_binary(str(tmp_path / "nope")) is None

    def test_non_executable_is_not_a_binary(self, tmp_path):
        p = tmp_path / "cua-driver"
        p.write_text("x")
        p.chmod(p.stat().st_mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)
        assert driver.resolve_binary(str(p)) is None

    def test_falls_back_to_path(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path / "cua-driver")
        monkeypatch.setattr(driver.shutil, "which", lambda n: b if n == "cua-driver" else None)
        assert driver.resolve_binary("") == b

    def test_falls_back_to_local_bin_when_path_misses(self, tmp_path, monkeypatch):
        """The installer's default target is routinely off a service's PATH."""
        monkeypatch.setattr(driver.shutil, "which", lambda _: None)
        local = tmp_path / ".local" / "bin"
        local.mkdir(parents=True)
        b = _fake_binary(local / "cua-driver")
        monkeypatch.setattr(driver, "_FALLBACK_DIRS", (str(local),))
        assert driver.resolve_binary("") == b

    def test_returns_none_when_absent(self, monkeypatch):
        monkeypatch.setattr(driver.shutil, "which", lambda _: None)
        monkeypatch.setattr(driver, "_FALLBACK_DIRS", ())
        assert driver.resolve_binary("") is None


class TestToolFilter:
    def test_core_is_an_allowlist(self):
        f = driver.tool_filter({"tool_surface": "core"})
        assert f["include"] == list(driver.CORE_TOOLS)

    def test_core_is_the_default(self):
        assert driver.tool_filter({}) == driver.tool_filter({"tool_surface": "core"})

    def test_full_binds_everything(self):
        assert driver.tool_filter({"tool_surface": "full"}) is None

    def test_extra_tools_extend_core(self):
        f = driver.tool_filter({"tool_surface": "core", "extra_tools": ["kill_app"]})
        assert "kill_app" in f["include"]
        assert set(driver.CORE_TOOLS) <= set(f["include"])

    def test_extra_tools_are_deduped_and_ordered(self):
        f = driver.tool_filter({"extra_tools": ["click", "kill_app", "kill_app", " "]})
        assert f["include"].count("click") == 1
        assert f["include"].count("kill_app") == 1

    def test_extra_tools_ignored_when_full(self):
        assert driver.tool_filter({"tool_surface": "full", "extra_tools": ["kill_app"]}) is None

    def test_core_excludes_focus_stealing_and_destructive_tools(self):
        """The driver's whole point is not stealing focus (ADR 0084 D3)."""
        for t in ("bring_to_front", "kill_app", "check_for_update", "get_accessibility_tree"):
            assert t not in driver.CORE_TOOLS

    def test_core_carries_the_snapshot_tool(self):
        assert "get_window_state" in driver.CORE_TOOLS


class TestBuildEntry:
    def test_disabled_yields_no_server(self, tmp_path):
        b = _fake_binary(tmp_path / "cua-driver")
        assert driver.build_entry({"enabled": False, "binary_path": b}) is None

    def test_missing_key_is_disabled(self):
        assert driver.build_entry({}) is None

    def test_enabled_without_binary_yields_no_server(self, monkeypatch):
        """The plugin never installs the driver — absent means inert, not broken."""
        monkeypatch.setattr(driver.shutil, "which", lambda _: None)
        monkeypatch.setattr(driver, "_FALLBACK_DIRS", ())
        assert driver.build_entry({"enabled": True}) is None

    def test_entry_shape(self, tmp_path):
        b = _fake_binary(tmp_path / "cua-driver")
        e = driver.build_entry({"enabled": True, "binary_path": b})
        assert e["name"] == "cua-driver"
        assert e["transport"] == "stdio"
        assert e["command"] == b
        assert e["args"] == ["mcp"]

    def test_entry_is_persistent(self, tmp_path):
        """The per-(pid, window_id) element cache lives in the session's process."""
        b = _fake_binary(tmp_path / "cua-driver")
        assert driver.build_entry({"enabled": True, "binary_path": b})["persistent"] is True

    def test_entry_carries_the_allowlist(self, tmp_path):
        b = _fake_binary(tmp_path / "cua-driver")
        e = driver.build_entry({"enabled": True, "binary_path": b})
        assert e["tools"]["include"] == list(driver.CORE_TOOLS)

    def test_full_surface_omits_the_filter(self, tmp_path):
        b = _fake_binary(tmp_path / "cua-driver")
        e = driver.build_entry({"enabled": True, "binary_path": b, "tool_surface": "full"})
        assert "tools" not in e


class TestFactory:
    def test_factory_reads_live_config(self, tmp_path):
        b = _fake_binary(tmp_path / "cua-driver")

        class Cfg:
            plugin_config = {"cua": {"enabled": True, "binary_path": b}}

        assert driver.build_mcp_factory()(Cfg())["command"] == b

    def test_factory_tolerates_a_config_without_the_section(self):
        class Cfg:
            plugin_config = {}

        assert driver.build_mcp_factory()(Cfg()) is None

    def test_factory_tolerates_a_config_without_plugin_config(self):
        assert driver.build_mcp_factory()(object()) is None


class TestToolNameParsing:
    def test_json_array_of_strings(self):
        assert driver._tool_names('["click", "type_text"]') == ["click", "type_text"]

    def test_json_array_of_objects(self):
        assert driver._tool_names('[{"name": "click"}, {"name": "scroll"}]') == ["click", "scroll"]

    def test_json_object_with_tools_key(self):
        assert driver._tool_names('{"tools": [{"name": "click"}]}') == ["click"]

    def test_parses_the_real_list_tools_format(self):
        """Verbatim from `cua-driver list-tools` on 0.8.3 — one `name: description`
        per line. The first cut of this parser assumed whitespace-separated columns
        and silently returned [] against the real thing, which quietly disabled
        probe()'s mismatch check."""
        real = (
            "bring_to_front: Persistently activate an app so it genuinely holds macOS foreground\n"
            "click: Click against a target pid\n"
            "drag: Press-drag-release gesture from (from_x, from_y) to (to_x, to_y) — window-local\n"
            "get_window_state: Walk a running app's AX tree and return BOTH a structured `elements` array\n"
            "type_text: Insert text into the target pid via `AXSetAttribute(kAXSelectedText)`\n"
        )
        assert driver._tool_names(real) == ["bring_to_front", "click", "drag", "get_window_state", "type_text"]

    def test_descriptions_containing_colons_do_not_yield_extra_tools(self):
        """Real descriptions contain colons — only the line-start name counts."""
        line = "list_apps: List macOS apps — both running and installed — with per-app state flags:\n"
        assert driver._tool_names(line) == ["list_apps"]

    def test_skips_kebab_subcommands(self):
        """Tools are snake_case; management subcommands are kebab-case."""
        assert driver._tool_names("click: Click\ncheck-update: Check for a release\n") == ["click"]

    def test_skips_advisory_prose(self):
        """The driver prints advisory text on stderr, but never trust it not to
        reach stdout — a prose line must not become a tool name."""
        assert driver._tool_names("cua-driver-rs: reporting permission status only.\n") == []

    def test_garbage_does_not_raise(self):
        assert driver._tool_names("") == []


# Verbatim payloads from cua-driver 0.8.3. The first cut of this suite invented
# both shapes and passed against fiction while the real probe was broken.
GRANTED = """{
  "accessibility": true, "screen_recording": true, "screen_recording_capturable": true,
  "source": {"attribution": "driver-daemon", "disclaim_env": false,
             "note": "These booleans reflect the CuaDriver daemon's own TCC identity."}
}"""
NO_DAEMON = """{
  "daemon_running": false,
  "reason": "no CuaDriver daemon is running under the driver's own identity (com.trycua.driver), so its real TCC status can't be read from this process. Run `cua-driver permissions grant` to grant + verify.",
  "status": "unknown"
}"""
CALLER_ATTRIBUTED = """{
  "accessibility": true, "screen_recording": true, "screen_recording_capturable": true,
  "source": {"attribution": "caller", "disclaim_env": false,
             "note": "These booleans reflect the TCC identity of the app that launched this process."}
}"""


def _runner(tools_out, perms_rc=0, perms_out=GRANTED):
    def fake_run(binary, args, timeout=10.0):
        if args[0] == "list-tools":
            return 0, tools_out, ""
        return perms_rc, perms_out, ""

    return fake_run


def _tools_text(names):
    return "".join(f"{n}: does a thing\n" for n in names)


class TestProbe:
    def test_missing_binary_reports_actionably(self, monkeypatch):
        monkeypatch.setattr(driver.shutil, "which", lambda _: None)
        monkeypatch.setattr(driver, "_FALLBACK_DIRS", ())
        r = driver.probe({"enabled": True})
        assert r["ok"] is False
        assert "not found" in r["error"]

    def test_flags_configured_tools_the_driver_does_not_publish(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path / "cua-driver")
        monkeypatch.setattr(driver, "_run", _runner(_tools_text(["click", "get_window_state"])))
        r = driver.probe({"enabled": True, "binary_path": b, "extra_tools": ["not_a_real_tool"]})
        assert r["ok"] is True
        assert "not_a_real_tool" in r["error"]

    def test_uses_permissions_status_not_the_check_permissions_tool(self, tmp_path, monkeypatch):
        """check_permissions answers for the CALLER's TCC identity, so it reports
        `accessibility: true` while the driver itself has no grant. Only
        `permissions status` carries the driver's own identity."""
        b = _fake_binary(tmp_path / "cua-driver")
        calls = []

        def fake_run(binary, args, timeout=10.0):
            calls.append(list(args))
            return (0, _tools_text(driver.CORE_TOOLS), "") if args[0] == "list-tools" else (0, GRANTED, "")

        monkeypatch.setattr(driver, "_run", fake_run)
        driver.probe({"enabled": True, "binary_path": b})
        assert ["permissions", "status", "--json"] in calls
        assert not any("check_permissions" in a for a in calls)

    def test_granted_probe_has_no_notes(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path / "cua-driver")
        monkeypatch.setattr(driver, "_run", _runner(_tools_text(driver.CORE_TOOLS)))
        r = driver.probe({"enabled": True, "binary_path": b})
        assert r["ok"] is True
        assert r["error"] is None
        assert "17 tool(s) bound of 17 published" in r["identity"]

    def test_no_daemon_is_unknown_not_denied(self, tmp_path, monkeypatch):
        """Honest 'can't tell yet' — the old grep read `"disclaim_env": false` and
        cried missing-grant on a fully-granted machine."""
        b = _fake_binary(tmp_path / "cua-driver")
        monkeypatch.setattr(driver, "_run", _runner(_tools_text(driver.CORE_TOOLS), perms_out=NO_DAEMON))
        r = driver.probe({"enabled": True, "binary_path": b})
        assert "can't be confirmed" in r["error"]
        assert "permissions grant" in r["error"]

    def test_caller_attributed_grants_are_not_trusted(self, tmp_path, monkeypatch):
        """A `true` for the calling terminal says nothing about the driver."""
        b = _fake_binary(tmp_path / "cua-driver")
        monkeypatch.setattr(driver, "_run", _runner(_tools_text(driver.CORE_TOOLS), perms_out=CALLER_ATTRIBUTED))
        r = driver.probe({"enabled": True, "binary_path": b})
        assert "can't be confirmed" in r["error"]
        assert "caller" in r["error"]

    def test_denied_grant_is_reported(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path / "cua-driver")
        denied = GRANTED.replace('"accessibility": true', '"accessibility": false')
        monkeypatch.setattr(driver, "_run", _runner(_tools_text(driver.CORE_TOOLS), perms_out=denied))
        r = driver.probe({"enabled": True, "binary_path": b})
        assert "missing a macOS grant" in r["error"]
        assert "accessibility" in r["error"]

    def test_permissions_subcommand_absent_is_not_an_error(self, tmp_path, monkeypatch):
        """`permissions` is macOS-only — its absence elsewhere isn't a problem."""
        b = _fake_binary(tmp_path / "cua-driver")
        monkeypatch.setattr(
            driver, "_run", _runner(_tools_text(driver.CORE_TOOLS), perms_rc=2, perms_out="unknown subcommand")
        )
        r = driver.probe({"enabled": True, "binary_path": b})
        assert r["ok"] is True
        assert r["error"] is None

    def test_unparseable_tool_list_does_not_report_a_clean_bill(self, tmp_path, monkeypatch):
        """A parser miss must not masquerade as verification."""
        b = _fake_binary(tmp_path / "cua-driver")
        monkeypatch.setattr(driver, "_run", _runner("some unexpected banner\n"))
        r = driver.probe({"enabled": True, "binary_path": b})
        assert "unverified" in r["error"]

    def test_list_tools_failure_is_not_ok(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path / "cua-driver")
        monkeypatch.setattr(driver, "_run", lambda *a, **k: (1, "", "boom"))
        r = driver.probe({"enabled": True, "binary_path": b})
        assert r["ok"] is False
        assert "boom" in r["error"]
