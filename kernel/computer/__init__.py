"""kernel/computer/__init__.py — P4-A: one vision/act stack (J-06/J-07).

Kill List honored: this is THE computer-control path — `actions/computer_control.py`
(PyAutoGUI string-actions) stays legacy until the app layer migrates onto these
tools; no second stack is added, the first UIA-first one is built.

- observe: window enumeration + UIA tree dumps + text reads (READ risk)
- act: InputGateway — UIA-pattern verbs, handle-scoped, dry-run default
- tools: kernel Tool registrations behind PolicyEngine consent
"""

from kernel.computer.act import (
    RAW_VERBS,
    ActionPlan,
    ActionResult,
    InputGateway,
    VERBS,
)
from kernel.computer.observe import (
    DesktopError,
    ElementSnapshot,
    UIA_AVAILABLE,
    WindowInfo,
    dump_tree,
    find_elements,
    read_text,
    top_windows,
)
from kernel.computer.tools import (
    COMPUTER_TOOLS,
    RAW_TOOLS,
    build_computer_tools,
    build_power_tools,
    build_whatsapp_tool,
)

__all__ = [
    "ActionPlan", "ActionResult", "COMPUTER_TOOLS", "DesktopError",
    "ElementSnapshot", "InputGateway", "RAW_TOOLS", "RAW_VERBS",
    "UIA_AVAILABLE", "VERBS", "WindowInfo",
    "build_computer_tools", "build_power_tools", "build_whatsapp_tool",
    "dump_tree", "find_elements", "read_text", "top_windows",
]
