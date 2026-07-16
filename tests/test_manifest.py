"""The manifest is a contract with the host — parse it and hold it to that."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cua_plugin import driver

MANIFEST = Path(__file__).resolve().parents[1] / "protoagent.plugin.yaml"


@pytest.fixture(scope="module")
def m() -> dict:
    return yaml.safe_load(MANIFEST.read_text())


def test_parses(m):
    assert m["id"] == "cua"
    assert m["config_section"] == "cua"


def test_off_by_default(m):
    """The most dangerous capability we ship is not on by accident (ADR 0084)."""
    assert m["enabled"] is False
    assert m["config"]["enabled"] is False


def test_declares_no_pip_deps(m):
    """The whole point of the MCP wrapper: nothing enters the venv (ADR 0084 D1)."""
    assert not m.get("requires_pip")
    assert not m.get("optional_pip")


def test_defaults_to_the_core_surface(m):
    assert m["config"]["tool_surface"] == "core"


def test_settings_cover_every_config_key(m):
    """A key with no field is a key the operator can only edit by hand."""
    assert {s["key"] for s in m["settings"]} == set(m["config"])


def test_settings_types_are_supported(m):
    supported = {"string", "text", "number", "bool", "select", "string_list", "secret"}
    assert all(s["type"] in supported for s in m["settings"])


def test_select_fields_declare_options(m):
    for s in m["settings"]:
        if s["type"] == "select":
            assert s.get("options"), f"{s['key']} is a select with no options"


def test_tool_surface_options_match_the_code(m):
    """The dropdown and `tool_filter`'s branches must not drift apart."""
    field = next(s for s in m["settings"] if s["key"] == "tool_surface")
    assert set(field["options"]) == {"core", "full"}
    # Every offered option must mean something to the code: "full" is the only
    # value that disables the allowlist; anything else must produce one.
    assert driver.tool_filter({"tool_surface": "full"}) is None
    assert driver.tool_filter({"tool_surface": "core"}) is not None


def test_manifest_defaults_produce_the_core_allowlist(m):
    """Drift guard: the shipped defaults, fed through the real code, must bind
    exactly the core loop — not whatever a stale manifest happens to say."""
    assert driver.tool_filter(m["config"]) == {"include": list(driver.CORE_TOOLS)}


def test_shipped_defaults_are_inert(m):
    """The manifest's own defaults must not start a server on a fresh install."""
    assert driver.build_entry(m["config"]) is None


def test_depends_on_is_a_mapping_naming_a_real_sibling(m):
    """The host reads `depends_on` only when it's a mapping with a `key`
    (settings_schema: `isinstance(dep, dict) and dep.get("key")`). A bare string
    is silently ignored and the field renders unconditionally — a bug with no
    error message, so assert the shape."""
    keys = {s["key"] for s in m["settings"]}
    for s in m["settings"]:
        dep = s.get("depends_on")
        if dep is None:
            continue
        assert isinstance(dep, dict), f"{s['key']}: depends_on must be a mapping, got {type(dep).__name__}"
        assert dep.get("key"), f"{s['key']}: depends_on needs a `key`"
        # The short key is auto-prefixed with the section, so it must be a sibling.
        assert dep["key"] in keys, f"{s['key']}: depends_on names {dep['key']!r}, which isn't a field here"


def test_extra_tools_hides_when_it_would_be_ignored(m):
    """`tool_filter` ignores extra_tools under "full" — the UI must agree."""
    field = next(s for s in m["settings"] if s["key"] == "extra_tools")
    assert field["depends_on"] == {"key": "tool_surface", "equals": "core"}
    assert driver.tool_filter({"tool_surface": "full", "extra_tools": ["x"]}) is None


def test_stated_tool_count_matches_the_code(m):
    """Operator-facing copy makes a factual claim about how many tools `core`
    binds. Numbers in prose drift silently — pin it to the list."""
    import re

    field = next(s for s in m["settings"] if s["key"] == "tool_surface")
    claimed = re.search(r"(\d+) tools", field["description"])
    assert claimed, "the tool_surface description should state how many tools 'core' binds"
    assert int(claimed.group(1)) == len(driver.CORE_TOOLS)


def test_declares_no_secrets(m):
    """Nothing to leak — the driver is a local binary, not an API."""
    assert not m.get("secrets")


def test_test_button_is_declared(m):
    """Setup has real failure modes (missing binary, missing TCC) — make them checkable."""
    assert m["test"] is True


def test_description_states_the_fences_do_not_apply(m):
    """ADR 0071's lesson: never let the presented model imply containment we lack."""
    d = m["description"].lower()
    assert "do not apply" in d or "does not apply" in d
    assert "egress" in d and "fence" in d


def test_capabilities_do_not_claim_a_fence(m):
    assert m["capabilities"]["filesystem"] == "unfenced"
    assert m["capabilities"]["subprocess"] is True


def test_min_host_version_supports_the_seams_used(m):
    assert m["min_protoagent_version"] >= "0.100.0"
