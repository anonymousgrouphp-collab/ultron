"""kernel/home/ha.py — P4-C: Home Assistant via MCP-Assist (J-14), the mansion.

Research/08 §1 verdict: ONE integration, thousands of devices. HA runs an MCP
server (MCP-Assist pattern: dynamic entity discovery, ~95% token cut vs
dumping entity states into the prompt); the kernel mounts it through the SAME
P2-B client as every other MCP server — no second integration path.

Trust boundary (research/08 §1 + roadmap §3.4): "consent-gate write classes
(locks, alarms) — the house can lock doors and buy nothing without the policy
engine being live." Risk mapping is therefore structural, not per-call:

- discovery/read tools  (get_*, list_*, *_state)      → READ    (flow)
- control tools         (call_service, turn_on/off…)  → EXECUTE (consent)
- safety-critical       (lock/unlock/alarm/garage…)    → DESTRUCTIVE (default
                                                       policy DENIES outright;
                                                       a deployment that wants
                                                       them re-maps the policy)

Config gate (same shape as P2-D's consent gate): the wiring layer passes a
config dict; NOT configured = the mount is simply never created (clean
ValueError if called anyway); a broken mount raises the P2-B clean error.
The access token travels in the Authorization header and is never logged.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping

from kernel.mcp_client import McpMount, mount_http
from kernel.types import RiskClass

log = logging.getLogger(__name__)

__all__ = ["MOUNT_URL_PATH", "ha_risk_overrides", "mount_home_assistant"]

# MCP-Assist's default serve path on the HA box (community integration)
MOUNT_URL_PATH = "/mcp_server"

# tool-name → risk map, applied on top of the mount's untrusted default.
# Order matters: first matching pattern wins; safety-critical beats control.
_RISK_RULES: tuple[tuple[str, RiskClass], ...] = (
    (r"lock|unlock|alarm|garage|arm_|disarm", RiskClass.DESTRUCTIVE),
    (r"get_|list_|state|history|camera|snapshot|weather", RiskClass.READ),
    (r"call_service|turn_on|turn_off|toggle|set_|play|stop|pause|volume",
     RiskClass.EXECUTE),
)


def ha_risk_overrides(tool_names: list[str]) -> dict[str, RiskClass]:
    """Map HA MCP tool names to kernel risk classes (structural consent)."""
    out: dict[str, RiskClass] = {}
    for name in tool_names:
        for pattern, risk in _RISK_RULES:
            if re.search(pattern, name):
                out[name] = risk
                break
    return out


def _validate_config(config: Mapping[str, Any] | None) -> tuple[str, str]:
    if not config:
        raise ValueError(
            "Home Assistant is not configured — mount_home_assistant requires "
            "{'url': ..., 'token': ...} from the wiring layer")
    url = str(config.get("url") or "").strip()
    token = str(config.get("token") or "").strip()
    if not url:
        raise ValueError("Home Assistant config is missing 'url'")
    if not token:
        raise ValueError("Home Assistant config is missing 'token'")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("Home Assistant 'url' must be an http(s) endpoint")
    return url, token


async def mount_home_assistant(config: Mapping[str, Any] | None, registry,
                               *, prefix: str = "ha",
                               timeout_s: float = 30.0) -> McpMount:
    """Mount the HA MCP server into `registry` with the structural risk map.

    The mount default is EXECUTE (untrusted, per P2-B doctrine); discovery
    tools are relaxed to READ here; safety-critical names are escalated to
    DESTRUCTIVE. Policy gating stays at the same choke point as every other
    caller — this function never bypasses or weakens it."""
    url, token = _validate_config(config)
    if not url.rstrip("/").endswith(MOUNT_URL_PATH):
        url = url.rstrip("/") + MOUNT_URL_PATH
    headers = {"Authorization": f"Bearer {token}"}
    # first connect WITHOUT installing, to learn the tool names for the
    # risk map (the mount's own discovery is the source of truth).
    # McpMount.tools returns the WRAPPED kernel tools (ha_<remote>), so the
    # prefix is stripped to recover remote names; risk_overrides are keyed
    # by REMOTE name — that is what McpMount._wrap looks up.
    probe = await mount_http(url, prefix=prefix, label="home-assistant",
                             headers=headers, timeout_s=timeout_s)
    overrides = ha_risk_overrides(
        [t.name.removeprefix(f"{prefix}_") for t in probe.tools])
    await probe.stop()
    log.info("home: mounting HA MCP with risk map %s",
             {k: v.value for k, v in sorted(overrides.items())})
    mount = await mount_http(url, prefix=prefix, label="home-assistant",
                             headers=headers, timeout_s=timeout_s,
                             risk_overrides=overrides)
    mount.into(registry)
    return mount
