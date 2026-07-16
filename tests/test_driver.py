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

    def test_plain_text_fallback(self):
        assert "click" in driver._tool_names("click  Click an element\ntype_text  Type\n")

    def test_text_fallback_skips_kebab_subcommands(self):
        """Tools are snake_case; management subcommands are kebab-case."""
        assert "check-update" not in driver._tool_names("click\ncheck-update\n")

    def test_garbage_does_not_raise(self):
        assert driver._tool_names("") == []


class TestProbe:
    def test_missing_binary_reports_actionably(self, monkeypatch):
        monkeypatch.setattr(driver.shutil, "which", lambda _: None)
        monkeypatch.setattr(driver, "_FALLBACK_DIRS", ())
        r = driver.probe({"enabled": True})
        assert r["ok"] is False
        assert "not found" in r["error"]

    def test_flags_configured_tools_the_driver_does_not_publish(self, tmp_path, monkeypatch):
        """CORE_TOOLS is inferred from upstream source, so the probe must verify it."""
        b = _fake_binary(tmp_path / "cua-driver")
        calls = []

        def fake_run(binary, args, timeout=10.0):
            calls.append(args[0])
            if args[0] == "list-tools":
                return 0, '["click", "get_window_state"]', ""
            return 0, '{"accessibility": true}', ""

        monkeypatch.setattr(driver, "_run", fake_run)
        r = driver.probe({"enabled": True, "binary_path": b, "extra_tools": ["not_a_real_tool"]})
        assert r["ok"] is True
        assert "not_a_real_tool" in r["error"]
        assert "check_permissions" in calls

    def test_reports_denied_permissions(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path / "cua-driver")

        def fake_run(binary, args, timeout=10.0):
            if args[0] == "list-tools":
                return 0, '["click"]', ""
            return 0, '{"accessibility": false}', ""

        monkeypatch.setattr(driver, "_run", fake_run)
        r = driver.probe({"enabled": True, "binary_path": b})
        assert "grant" in (r["error"] or "").lower()

    def test_list_tools_failure_is_not_ok(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path / "cua-driver")
        monkeypatch.setattr(driver, "_run", lambda *a, **k: (1, "", "boom"))
        r = driver.probe({"enabled": True, "binary_path": b})
        assert r["ok"] is False
        assert "boom" in r["error"]

    def test_clean_probe_has_no_notes(self, tmp_path, monkeypatch):
        b = _fake_binary(tmp_path / "cua-driver")
        published = list(driver.CORE_TOOLS)

        def fake_run(binary, args, timeout=10.0):
            if args[0] == "list-tools":
                import json as _json

                return 0, _json.dumps(published), ""
            return 0, '{"accessibility": true, "screen_recording": true}', ""

        monkeypatch.setattr(driver, "_run", fake_run)
        r = driver.probe({"enabled": True, "binary_path": b})
        assert r["ok"] is True
        assert r["error"] is None
