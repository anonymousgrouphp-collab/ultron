# ULTRON Product Requirements Document

## Product statement

ULTRON is a local-first Windows desktop assistant that helps one person research,
understand their computer, and carry out explicitly approved tasks through voice,
text, and a local dashboard. It must be trustworthy before it is autonomous.

## Problem and user

The user wants hands-free help without surrendering control of their computer,
files, accounts, or messages. Existing assistants either answer questions without
acting, or automate a desktop without a reliable safety boundary. ULTRON closes
that gap by making every effect visible, attributable, and reversible where
possible.

The initial product serves one technically comfortable Windows 11 user on their
own device. Multi-user, team, cloud-hosted, and household deployments are not
part of the first release.

## Product principles

1. Safety is a product feature. A request to write, execute, send, purchase,
   delete, or change settings is never silently performed.
2. One request has one path. Every tool call crosses the same typed kernel,
   policy, audit, timeout, and result-rendering seam.
3. Be useful while failing safely. Read-only research and inspection should work
   even when credentials, consent UI, or an integration is unavailable.
4. State what happened, not what was intended. ULTRON reports observed outcomes
   and clean failures; it never speaks raw stack traces or provider errors.
5. Reliability beats spectacle. A narrow workflow with an evaluation is more
   valuable than a large unmeasured action catalogue.

## Release 1 scope

### In scope

- Voice, text, and local-dashboard requests enter a single session model.
- Read-only tools: weather, web research, system status, and screen capture with
  visible capture indication.
- A typed Tool contract: JSON-schema parameters, risk class, timeout, structured
  result, event trace, and SQLite audit record.
- A visible consent prompt for write and execute tools that shows the exact tool,
  arguments summarized for humans, impact, and cancel option.
- A local activity view: request, proposed action, consent decision, result, and
  audit history.
- Ten scripted end-to-end tasks with deterministic assertions.

### Explicitly out of scope for Release 1

- Autonomous browsing, form submission, purchases, social-media messaging, and
  account login automation.
- Code generation or execution on the user's machine. This returns only after a
  sandboxed P2 coding workflow exists.
- Automatic process termination, shutdown, restart, or configuration changes.
- LAN/cloud dashboard access without TLS, normal authentication, rate limiting,
  and a security review.
- Multi-agent orchestration, plugins, marketplace, house control, camera
  monitoring, and proactive autonomy.

## Core journeys

| Journey | Expected outcome | Safety requirement |
|---|---|---|
| “What is my CPU and memory use?” | ULTRON presents current metrics and source time. | No consent; audit as read. |
| “Research X and summarize sources.” | ULTRON returns a cited summary and links. | Network use is disclosed; no account actions. |
| “Look at my screen.” | ULTRON indicates capture, then describes the current screen. | Capture is visible and auditable. |
| “Create a note on Desktop.” | ULTRON previews path and contents, then waits for approval. | One-use consent; result includes the created path. |
| “Send this message.” | ULTRON renders recipient, service, and exact message before sending. | One-use consent; no keyboard-driven blind send. |

## Functional requirements

### Request and execution

- Each tool call has an ID, source, typed arguments, risk class, deadline, and
  structured result.
- The runtime denies unknown tools and never executes a handler outside the
  registry.
- Read tools may execute without consent. Write and execute tools require a live,
  user-originated consent response. Destructive tools are denied by default.
- A failure returns a concise user-safe message. Full exception details stay in
  local diagnostic logs only.
- Audit records are written for allow, deny, consent accepted, consent rejected,
  timeout, and handler failure.

### Consent

- The prompt identifies the requested effect in plain language and allows only
  approve once or cancel.
- Approval expires after 60 seconds, cannot be reused, and is bound to the tool
  call ID and normalized arguments.
- A dashboard, voice transcript, LLM response, or `confirmed` parameter cannot
  serve as consent.

### Privacy and local data

- Secrets stay outside source control and are never sent to logs or the dashboard.
- ULTRON documents when file content, screen data, or text is sent to a cloud
  model before that transfer occurs.
- The audit database and future memory database are local runtime data and are
  excluded from version control.

## Non-functional requirements

- Supported runtime: Windows 11 and CPython 3.13 in a project virtual environment.
- Tool invocations must not block the event loop; each has a timeout and no
  automatic retries for side-effecting work.
- Test suite order: static checks, unit tests, integration tests, then a small
  critical-path end-to-end suite.
- No release is approved with advisory static checks failing on new or changed
  files.

## Success measures and release gate

- 10/10 scripted Release 1 workflows pass through the production kernel path.
- 0 destructive operations execute without consent in the scenario suite.
- 100% of tool decisions produce an audit record in integration tests.
- p95 time from a text request to first tool decision is measured and published;
  Release 1 target is under 2 seconds excluding third-party service latency.
- Fresh-clone setup and launch are tested on Windows 11 with Python 3.13.

## Delivery sequence

1. Finish P1-F: port or quarantine all legacy handlers behind the kernel.
2. Finish P1-C/P1-G/P1-H: gateway, agent loop, then voice as a kernel client.
3. Build the consent UI and ten production-path evaluation scenarios.
4. Only then re-enable narrowly scoped write tools and consider memory or MCP.
