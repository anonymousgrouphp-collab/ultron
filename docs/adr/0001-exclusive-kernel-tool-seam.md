# Kernel is the exclusive tool execution seam

**Status: accepted.** ULTRON will execute every capability through the typed Tool
registry and PolicyEngine rather than dispatching `actions/` functions from voice,
dashboard, or model code. This is a deliberate migration cost: it is the only way
to apply consent, audit, timeouts, structured results, and future MCP exposure
consistently without maintaining parallel safety implementations.
