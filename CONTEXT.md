# ULTRON Context

ULTRON is a local-first, single-user desktop assistant for Windows. Its purpose
is to turn a user request into an observable, policy-controlled tool outcome.

## Language

**Session**:
One continuous interaction between a user and ULTRON, regardless of whether its
input arrives through voice, text, or the local dashboard.
_Avoid_: Conversation, chat

**Tool**:
A named, schema-defined capability registered with ULTRON's kernel, including a
risk class and execution budget.
_Avoid_: Action, function, handler

**Tool call**:
One request to execute a Tool, with a caller-generated ID, arguments, and source.
_Avoid_: Command, function call

**Consent**:
An explicit approval by the user for one Tool call after its intended effect is
shown. Model output, a repeated request, and a tool argument are not Consent.
_Avoid_: Confirmation flag, `confirmed=yes`

**Audit record**:
An append-only local record of a Tool call's risk, policy decision, and outcome.
_Avoid_: Log message, history

**Legacy handler**:
An existing function under `actions/` that predates the Tool kernel and is being
adapted or retired during Phase 1.
_Avoid_: Tool
