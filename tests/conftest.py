"""Test bootstrap — the plugin must import and run with NO protoAgent host.

The host imports a plugin's ``__init__.py`` **as a package** (see
``_load_plugin_module`` in graph/plugins/loader.py) so that ``from .driver import
…`` resolves. Mirror that here rather than importing ``__init__`` as a top-level
module, which would break the relative imports the real loader supports.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = "cua_plugin"

if PKG not in sys.modules:
    _spec = importlib.util.spec_from_file_location(PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[PKG] = _mod
    _spec.loader.exec_module(_mod)


class FakeRegistry:
    """Mirrors the slice of ``PluginRegistry`` this plugin touches."""

    plugin_id = "cua"

    def __init__(self, config: dict | None = None, live: dict | None = None):
        self.config = dict(config or {})
        self._live = dict(live if live is not None else (config or {}))
        self.plugin_dir = str(ROOT)
        self.mcp_servers: list = []
        self.skill_dirs: list = []
        self.routers: list = []
        self.tools: list = []

    def live_config(self) -> dict:
        return dict(self._live)

    def register_mcp_server(self, factory) -> None:
        assert callable(factory), "the host drops a non-callable factory with a warning"
        self.mcp_servers.append(factory)

    def register_skill_dir(self, path) -> None:
        self.skill_dirs.append(path)

    def register_router(self, router, prefix=None) -> None:
        # (prefix, router) mirrors graph/plugins/testkit.py's convention. The real
        # registry stores {"router", "prefix"} and resolves the effective prefix;
        # the host-free fakes both simplify, so follow the sanctioned one.
        self.routers.append((prefix, router))

    def register_tool(self, tool) -> None:
        self.tools.append(tool)

    def register_tools(self, tools) -> None:
        self.tools.extend(tools)


@pytest.fixture
def registry():
    return FakeRegistry()
