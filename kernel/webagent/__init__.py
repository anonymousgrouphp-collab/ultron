"""kernel/webagent — research/12 D1: the computer-use web agent (J-05).

ada_v2's `web_agent.py` protocol, rebuilt on ULTRON's seams:
- model decisions come from the gateway (kernel/gateway/cu.py — the CU model
  string lives there, Kill List #3);
- the browser is Playwright's headless Chromium (lazy import — the kernel
  package stays light, and tests inject a fake page);
- the tool layer (tools.py) is the only registration point; every run is a
  WRITE-risk action, so the D2 confirmation gate asks the human first.
"""

from kernel.webagent.agent import WebAgent
from kernel.webagent.tools import build_web_agent_tools

__all__ = ["WebAgent", "build_web_agent_tools"]
