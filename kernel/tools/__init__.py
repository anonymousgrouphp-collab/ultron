"""kernel.tools — P1-B: the tool kernel.

`ToolRegistry` is the single execution choke point (timeout/retry/structured
results/optional bus events). `Tool` is the record: JSON-schema parameters,
risk class, execution budget. The P1-E policy engine gates ToolRegistry.execute;
the P2-A FastMCP server serves registry.declarations() + execute over MCP.
"""

from kernel.tools.base import Handler, Tool
from kernel.tools.registry import ToolRegistry, default_registry

__all__ = ["Handler", "Tool", "ToolRegistry", "default_registry"]
