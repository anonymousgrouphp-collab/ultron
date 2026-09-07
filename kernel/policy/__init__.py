"""kernel.policy — P1-E: safety as a subsystem (roadmap §3.4).

PolicyEngine gates every tool execution through a Policy (RiskClass → Decision,
fail-safe: ASK with no consent handler = DENY) and records every decision in an
AuditLog. ToolRegistry stays pure; the agent loop calls policy.run() instead of
registry.execute().
"""

from kernel.policy.audit import AuditLog
from kernel.policy.engine import ConsentCallback, Decision, Policy, PolicyEngine

__all__ = ["AuditLog", "ConsentCallback", "Decision", "Policy", "PolicyEngine"]
