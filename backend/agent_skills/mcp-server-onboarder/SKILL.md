---
name: mcp-server-onboarder
description: >
  Onboard, validate, and operationalize new MCP servers in the dashboard control plane.
  Use for requests to add/register/connect a new MCP server, verify connectivity, confirm tools,
  and update server availability safely.
---

# MCP Server Onboarder

Use this workflow whenever the user asks to add or connect a new MCP server.
This includes follow-up requests in the same chat session (for example: "add another server").

## Preferred Tools

- `mcp_server_discover`
- `mcp_server_onboard`
- `mcp_servers_list`
- `mcp_server_upsert`
- `mcp_server_test`
- `mcp_tools_list_by_server`
- `mcp_server_disable`
- `mcp_stdio_bridge_plan`
- `mcp_stdio_bridge_start`
- `mcp_stdio_bridge_status`
- `mcp_stdio_bridge_stop`

## Grounding Rules (Mandatory)

1. Every onboarding cycle must begin with a real `mcp_servers_list` tool call.
2. Never claim tool availability, tool absence, or server status from memory alone.
3. If you state a tool is unavailable, include the exact error from a current-turn tool call attempt.
4. For "add another server" in the same session, restart from Step 1 and re-check current registry state.
5. Do not provide manual shell fallback steps unless:
   - the user explicitly asks for manual mode, or
   - a required tool call fails and you include the exact failure.
6. If a response was generated without onboarding tool calls, do not continue onboarding logic. Retry with explicit tool calls first.

## Goals

1. Add or update server registration in dashboard registry.
2. Run connection handshake test (`ping`, `initialize`, `tools/list`).
3. Verify tools appear for the new server in catalog.
4. Return explicit pass/fail status and next action.

## Input Contract

Extract or request these fields:

- `name`: stable server alias (for example `chicago-opendata`)
- `endpoint`: MCP HTTP endpoint (`https://.../mcp`)
- `description`: short operator label
- `enabled`: default `true` unless user says otherwise
- `headers_env` preferred over raw `headers` for secrets

If endpoint is missing or ambiguous, ask one short clarifying question.

## Execution Flow

1. Baseline inventory:
   - Call `mcp_servers_list` to understand current state and avoid duplicate naming.
2. Discovery and recommendation:
   - Convert user intent into short search phrases before discovery calls.
   - Call `mcp_server_discover` with one short phrase at a time (1-3 words per query).
   - Prefer direct canonical phrases such as `finance`, `stock market`, `crypto`, `housing`, `NYC housing`.
   - Avoid long sentence-style queries or multi-clause strings.
   - If the first query returns no candidates, retry with 1-2 alternate short phrases derived from the same intent.
   - Keep a `queries_used` list for transparency.
   - Return top options with one recommendation, including `auth_requirement` and verification score/verdict.
   - Ask the user for explicit confirmation before any registry mutation.
3. If recommended candidate is `stdio_bridge_required`:
   - Prefer `mcp_stdio_bridge_start` with `auto_onboard=true` and `confirmed=true`.
   - If auto-start is unavailable, call `mcp_stdio_bridge_plan` for manual bridge config/commands, then onboard local endpoint.
4. Primary onboarding (only after confirmation):
   - Call `mcp_server_onboard` with `confirmed=true` and normalized payload.
5. If onboarding returns failure:
   - Call `mcp_server_test` for detailed stage diagnostics.
   - If user requested rollback or if explicitly unsafe to keep enabled, call `mcp_server_disable`.
6. Final verification:
   - Call `mcp_tools_list_by_server` for the server and confirm non-zero tools.
7. Report:
   - Provide a concise operator summary (status, server id/name, tool count, errors/remediation).

## Same-Session Multi-Server Rule

When the user asks to onboard an additional server after a successful onboarding in the same chat:

1. Run a fresh `mcp_servers_list` again (do not reuse prior results).
2. Treat the next server as a brand-new onboarding cycle.
3. Require new discovery/test/tool-list evidence for the new server before reporting success.

## Safety Rules

1. Do not invent endpoints, headers, or auth tokens.
2. Prefer `headers_env` over literal secrets.
3. Treat onboarding as failed unless connection test is OK and tools are visible.
4. Require explicit confirmation before mutation (`mcp_server_onboard` with `confirmed=true`).
5. If onboarding fails, provide exact failing stage and suggested fix.
6. Keep existing servers untouched unless user asks to modify them.

## Response Template

Use this output format:

### MCP Onboarding Result

- `status`: `passed` or `failed`
- `server`: `<name> (<id>)`
- `endpoint`: `<normalized endpoint>`
- `auth_requirement`: `<no_auth_required|auth_required|unknown>`
- `test_stage`: `<complete|ping|initialize|tools/list|...>`
- `tool_count`: `<n>`
- `actions_taken`: `<created/updated/disabled>`
- `next_step`: `<operator action>`
- `grounding`: `<tools_executed + current-turn evidence>`
- `queries_used`: `<ordered discovery queries actually executed>`

If failed, include `error.code`, `error.message`, and one remediation bullet.

## References

- `references/server_manifest_schema.md`
- `references/onboarding_runbook.md`
- `references/discovery_sources.md`
