# MCP Onboarding Runbook

This runbook defines pass/fail criteria for MCP server onboarding.

## Standard Sequence

1. `mcp_servers_list`
2. `mcp_server_discover` using short intent phrases (1-3 words each), one query per call
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

## Grounding Requirement

1. Each onboarding cycle must start with a fresh `mcp_servers_list` call.
2. Do not reuse previous cycle output for "add another server" requests.
3. Do not assert missing tools unless a current-turn tool call returned an explicit error.
4. If no onboarding tools were called in the turn, retry tool execution before producing a diagnostic summary.
5. Discovery queries should be short and direct (for example `finance`, `stock market`, `nyc housing`), not sentence-length prompts.
6. Include `queries_used` in operator output for discovery turns.

## Success Criteria

Onboarding is considered successful only if:

1. Registry upsert succeeds (`action` is `created` or `updated`).
2. Connection test returns `ok: true`.
3. Tool listing for the target server returns `tool_count > 0`.
4. For stdio-origin servers, a bridge plan exists and onboarding uses the bridged HTTP endpoint.

## Failure Diagnostics

Typical failing stages:

1. `ping`
- Endpoint reachable but strict server may reject `ping`.
- Check whether `initialize` and `tools/list` still pass.

2. `initialize`
- Often indicates invalid MCP endpoint path, missing bridge for stdio-only servers, or auth issue.

3. `tools/list`
- Server may initialize but not expose tools due to capability mismatch or auth scope.

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
