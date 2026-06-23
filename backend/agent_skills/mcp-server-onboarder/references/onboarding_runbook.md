# MCP Onboarding Runbook

This runbook defines pass/fail criteria for MCP server onboarding.

## Standard Sequence

1. `mcp_servers_list`
2. `mcp_server_discover` using single-word queries whenever possible (two words only when unavoidable), one query per call
3. User confirmation on selected option
4. If recommendation indicates `stdio_bridge_required`:
   - Prefer `mcp_stdio_bridge_start` (`auto_onboard=true`, `confirmed=true`)
   - Optionally use `mcp_stdio_bridge_status` to verify runtime health
   - If needed, use `mcp_stdio_bridge_stop` for cleanup/retry
   - Fallback: run `mcp_stdio_bridge_plan` for manual steps
5. `mcp_server_onboard` with `confirmed=true`
6. `mcp_tools_list_by_server`
7. Optional failure path:
   - `mcp_server_test`
   - `mcp_server_disable`
8. Post-onboarding note: Newly registered server tools appear in the conversation via system-reminder on the *next* user turn, not immediately. If tools are needed right away, inform the user that a follow-up message will activate them. Do not attempt to call tools from a newly-onboarded server in the same turn as onboarding.

## Grounding Requirement

1. Each onboarding cycle must start with a fresh `mcp_servers_list` call.
2. Do not reuse previous cycle output for "add another server" requests.
3. Do not assert missing tools unless a current-turn tool call returned an explicit error.
4. If no onboarding tools were called in the turn, retry tool execution before producing a diagnostic summary.
5. Discovery queries must be single words whenever possible — never sentence-length prompts.
6. Include `queries_used` in operator output for discovery turns.
7. If any planned query is longer than 2 words, rewrite it before calling `mcp_server_discover`.
8. Do not batch multiple phrases into one query string.

## Success Criteria

Onboarding is considered successful only if:

1. Registry upsert succeeds (`action` is `created` or `updated`).
2. Connection test returns `ok: true`.
3. Tool listing for the target server returns `tool_count > 0`.
4. For stdio-origin servers, a bridge plan exists and onboarding uses the bridged HTTP endpoint.
5. For stdio bridges, verify that at least one listed tool name is semantically consistent with the server's stated purpose (e.g. a Reddit server should expose tools with names like `search_reddit`, `get_top_posts` — not `openfda_count`). A mismatch indicates port collision or wrong process attached; treat as failed.

## Failure Diagnostics

Typical failing stages:

1. `ping`
- Endpoint reachable but strict server may reject `ping`.
- Check whether `initialize` and `tools/list` still pass.

2. `initialize`
- Often indicates invalid MCP endpoint path, missing bridge for stdio-only servers, or auth issue.

3. `tools/list`
- Server may initialize but not expose tools due to capability mismatch or auth scope.

## Bridge Startup Failure Diagnostics

When bridge fails to start ("Bridge failed to become ready"):

1. Call `mcp_stdio_bridge_stop` to retrieve `log_tail` from the response.
2. Parse `log_tail` for known patterns:
   - `"Cannot find package"` or `"ERR_MODULE_NOT_FOUND"` → missing peer dependency → install the missing package manually then retry
   - `"Unsupported engine"` → Node/Python version mismatch → note the required version and check environment compatibility
   - `"address already in use"` → port collision → retry `mcp_stdio_bridge_start` with `bridge_port` incremented by 1
   - `"Connection closed"` immediately on startup → process exited cleanly but unexpectedly → check command/args spelling and package name
3. If `log_tail` is unavailable, re-attempt with verbose env vars or check system logs.

## Remote Endpoint Failure Patterns

When `mcp_server_onboard` returns an HTTP error on a remote endpoint:

| HTTP | Body Signal                         | Diagnosis                   | Action                                    |
|------|-------------------------------------|-----------------------------|-------------------------------------------|
| 403  | cloudflare / 1010 / browser sig     | CDN bot block               | Skip remote; fall back to stdio bridge    |
| 401  | api-token-missing / apify           | Platform wrapper token req  | Skip; use stdio package directly          |
| 401  | Generic unauthorized                | Auth header missing         | Check `headers_env` config                |
| 000  | Empty / timeout                     | Network unreachable         | Verify endpoint URL and DNS               |

When a 403 CDN block or platform-token 401 is detected, automatically proceed to the stdio bridge without requiring the user to re-confirm — note the fallback in your response.

## Bridge Lifecycle and Persistence

Stdio bridges started via `mcp_stdio_bridge_start` are session-scoped:

- They do not survive process restarts or session timeouts.
- After reconnecting to an interrupted session, always run `mcp_stdio_bridge_status` before assuming bridge-dependent tools are available.
- If a bridge has died, re-run `mcp_stdio_bridge_start` with the same config to restore it.
- Bridges started outside the bridge manager (e.g. via shell `nohup`) are invisible to `mcp_stdio_bridge_status` — treat those ports as potentially occupied when starting new bridges.

## Remediation Playbook

If onboarding fails:

1. Confirm endpoint path includes `/mcp`.
2. Confirm auth header names and token source (`headers_env` variables exist).
3. If server is stdio-only, run `mcp_stdio_bridge_start` (or `mcp_stdio_bridge_plan` fallback) and confirm bridge is running.
4. Re-run `mcp_server_test`.
5. If still failing and user requests rollback, run `mcp_server_disable`.

## Operator Output Requirements

Always include:

1. Server name and id.
2. Endpoint used.
3. Test stage/status.
4. Tool count.
5. Explicit next step.
