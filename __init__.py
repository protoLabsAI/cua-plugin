"""cua — computer use for protoAgent, via the third-party cua-driver binary.

Registers a managed MCP server (ADR 0019) that spawns `cua-driver mcp` over
stdio, so the driver's GUI-automation tools become agent tools without a single
package entering the venv. The driver runs out-of-process — the shape ADR 0027 D1
prescribes for dangerous code — and this plugin never installs it.

What enabling this actually costs you is in ADR 0084: protoAgent's containment is
in-process (the egress allowlist lives inside `fetch_url`, the filesystem fence
inside `fs_tools`), and a mouse click reaches none of it.

Host-only imports stay lazy so the test suite runs with no protoAgent host.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.cua")


def _test_router(registry):
    """`POST /api/config/test-cua` — the Settings "Test connection" button.

    Mounted at the convention path with `prefix=""`, the same escape hatch the
    core chat-surface wirer uses: the button's URL is fixed at
    `/api/config/test-<section>`, which no `/api/plugins/<id>` prefix can produce.
    Bearer-gated by default (it isn't in `public_paths`).
    """
    from fastapi import APIRouter

    from .driver import probe

    router = APIRouter()

    @router.post("/api/config/test-cua")
    async def _test(body: dict | None = None):
        # live_config(), not registry.config: a router mounted at register time
        # can't be re-mounted on reload, so the snapshot goes stale. The body wins
        # over both — the point of the button is checking a path before saving it.
        section = dict(registry.live_config() or {})
        return probe({**section, **(body or {})})

    return router


def register(registry) -> None:
    try:
        from .driver import build_mcp_factory

        registry.register_mcp_server(build_mcp_factory())
    except Exception:  # noqa: BLE001 — one failing contribution must not sink the rest
        log.exception("[cua] registering the MCP server failed")

    try:
        # The snapshot-before-and-after invariant is not optional and fails
        # silently when skipped (ADR 0084 D4) — the tools are close to unusable
        # without this.
        registry.register_skill_dir("skills")
    except Exception:  # noqa: BLE001
        log.exception("[cua] registering skills failed")

    try:
        registry.register_router(_test_router(registry), prefix="")
    except Exception:  # noqa: BLE001
        log.exception("[cua] registering the test route failed")

    log.info("[cua] registered")
