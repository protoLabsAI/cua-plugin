"""register() is the host's only entry point — it must never raise, and must
contribute exactly what the manifest promises."""

from __future__ import annotations

from pathlib import Path

import pytest

import cua_plugin
from conftest import FakeRegistry


@pytest.fixture
def registered(registry):
    cua_plugin.register(registry)
    return registry


def test_registers_an_mcp_server(registered):
    assert len(registered.mcp_servers) == 1


def test_registers_the_skill_dir(registered):
    assert registered.skill_dirs == ["skills"]


def test_the_skill_dir_exists(registered):
    root = Path(registered.plugin_dir)
    assert (root / "skills" / "driving-native-apps" / "SKILL.md").is_file()


def test_registers_the_test_route_at_the_convention_path(registered):
    """`/api/config/test-<section>` is a fixed URL; prefix="" is the sanctioned
    escape hatch (the core chat-surface wirer uses the same one)."""
    _, prefix = registered.routers[0]
    assert prefix == ""
    paths = [r.path for r in registered.routers[0][0].routes]
    assert "/api/config/test-cua" in paths


def test_registers_no_tools(registered):
    """Every tool arrives from the MCP server, namespaced `cua-driver__*`."""
    assert registered.tools == []


def test_factory_is_inert_by_default(registered):
    """Registered but disabled => nothing spawns."""

    class Cfg:
        plugin_config = {"cua": {"enabled": False}}

    assert registered.mcp_servers[0](Cfg()) is None


def test_register_survives_a_broken_contribution(monkeypatch):
    """One failing seam must not sink the rest — the host calls register() once."""
    monkeypatch.setattr(cua_plugin, "_test_router", lambda _r: (_ for _ in ()).throw(RuntimeError("boom")))
    r = FakeRegistry()
    cua_plugin.register(r)  # must not raise
    assert len(r.mcp_servers) == 1
    assert r.skill_dirs == ["skills"]


def test_test_route_prefers_live_config_over_the_snapshot(monkeypatch):
    """A mounted router can't be re-mounted on reload, so the snapshot goes stale."""
    from cua_plugin import driver

    seen = {}

    def fake_probe(section):
        seen.update(section)
        return {"ok": True}

    monkeypatch.setattr(driver, "probe", fake_probe)

    r = FakeRegistry(config={"binary_path": "/stale"}, live={"binary_path": "/fresh"})
    router = cua_plugin._test_router(r)
    endpoint = next(rt.endpoint for rt in router.routes if rt.path == "/api/config/test-cua")

    import asyncio

    asyncio.run(endpoint(None))
    assert seen["binary_path"] == "/fresh"


def test_test_route_body_overrides_config(monkeypatch):
    """The button checks the path you're about to save, not the one on disk."""
    from cua_plugin import driver

    seen = {}
    monkeypatch.setattr(driver, "probe", lambda s: seen.update(s) or {"ok": True})

    r = FakeRegistry(live={"binary_path": "/saved"})
    router = cua_plugin._test_router(r)
    endpoint = next(rt.endpoint for rt in router.routes if rt.path == "/api/config/test-cua")

    import asyncio

    asyncio.run(endpoint({"binary_path": "/typed"}))
    assert seen["binary_path"] == "/typed"
