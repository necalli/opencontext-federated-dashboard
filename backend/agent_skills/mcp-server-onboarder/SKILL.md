---
name: mcp-server-onboarder
description: >
  Onboard, validate, and operationalize new MCP servers in the dashboard control plane.
  Use for requests to add/register/connect a new MCP server, verify connectivity, confirm tools,
  and update server availability safely. Discovery query policy is strict: prefer single-word
  MCP registry queries (two words only when necessary, never three or more), run one query per
  call, and report queries_used exactly as executed.
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

## Discovery Query Discipline (Mandatory)

1. `mcp_server_discover` queries MUST be single words whenever possible.
2. NEVER pass a sentence, clause, or multi-comma phrase as a discovery query.
3. Before every discovery call, distill user intent into the most precise single keyword.
4. Prefer single-word queries. Two-word queries are acceptable only when a single word is too broad. Never use three or more words. The MCP registry tokenizes poorly on long composite phrases — one precise keyword outperforms a sentence every time. Good examples: `reddit`, `openFDA`, `census`, `weather`, `NASA`. Poor examples: `federal data sources`, `reddit posts and comments`, `social media data`.
5. If a candidate query has more than 2 words, rewrite it before calling the tool.
6. If a query returns 0 results, retry with 1-2 alternate single-word synonyms or related terms (e.g. if `census` returns 0, try `demographics`, then `population`). Do not expand query length — shorten or reword instead.
7. Always include `queries_used` in final output, in exact execution order.

## Auth Classification

When evaluating and presenting candidates, classify auth using these tiers:

- `no_auth` — no credentials needed; works immediately out of the box
- `auth_optional` — server works without credentials; a key only improves rate limits or quota (e.g. a free API key that raises a daily request cap)
- `auth_required` — server is non-functional without credentials

If the user expresses an explicit auth preference (e.g. "no auth required"), treat it as a hard filter:
- Exclude `auth_required` candidates from the primary recommendation.
- List them separately under "excluded — requires auth" for transparency.
- Surface `auth_optional` candidates with a clear note explaining what the optional key provides and that the server works without it.

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
   - Distill user intent into the most precise single keyword before discovery calls.
   - Call `mcp_server_discover` with one word at a time (two words only when unavoidable).
   - Prefer direct canonical single-word terms such as `finance`, `crypto`, `housing`, `weather`.
   - Never use long sentence-style queries or multi-clause strings.
   - If the first query returns no candidates, retry with 1-2 alternate single-word synonyms.
   - Keep a `queries_used` list for transparency.
   - Return top options with one recommendation, including `auth_requirement` and verification score/verdict.
   - Apply any user auth preference as a hard filter before selecting the recommendation.
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

## Bridge Port Management

Stdio bridges each require a dedicated port. Port collisions produce silent false positives — the health check passes against the wrong server, and the wrong tools get reported. To prevent this:

1. Before calling `mcp_stdio_bridge_start`, check whether port 8300 (default) is already occupied by an existing bridge in the current session.
2. If any prior bridge in this session used port 8300, explicitly pass `bridge_port=8301` (or increment further for each additional server: 8302, 8303, etc.).
3. Each logical server must run on its own dedicated port — never share ports between bridges.

## Multi-Server Registry Integrity

When onboarding multiple servers in the same session, guard against one server's record silently overwriting another's:

1. After onboarding each server, call `mcp_servers_list` to confirm it appears as a separate named entry in the registry.
2. If two servers resolve to the same endpoint or port due to a collision, treat onboarding as failed and retry on a new port before proceeding.
3. Never allow a new server to silently overwrite an existing server's registry record — if names or endpoints overlap unexpectedly, surface the conflict and ask the user to confirm.

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
- `auth_requirement`: `<no_auth|auth_optional|auth_required|unknown>`
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
