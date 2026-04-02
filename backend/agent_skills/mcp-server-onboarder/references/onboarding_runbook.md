# MCP Onboarding Runbook

This runbook defines pass/fail criteria for MCP server onboarding.

## Standard Sequence

1. `mcp_servers_list`
2. `mcp_server_onboard`
3. `mcp_tools_list_by_server`
4. Optional failure path:
   - `mcp_server_test`
   - `mcp_server_disable`

## Success Criteria

Onboarding is considered successful only if:

1. Registry upsert succeeds (`action` is `created` or `updated`).
2. Connection test returns `ok: true`.
3. Tool listing for the target server returns `tool_count > 0`.

## Failure Diagnostics

Typical failing stages:

1. `ping`
- Endpoint reachable but strict server may reject `ping`.
- Check whether `initialize` and `tools/list` still pass.

2. `initialize`
- Often indicates invalid MCP endpoint path, incompatible server, or auth issue.

3. `tools/list`
- Server may initialize but not expose tools due to capability mismatch or auth scope.

## Remediation Playbook

If onboarding fails:

1. Confirm endpoint path includes `/mcp`.
2. Confirm auth header names and token source (`headers_env` variables exist).
3. Re-run `mcp_server_test`.
4. If still failing and user requests rollback, run `mcp_server_disable`.

## Operator Output Requirements

Always include:

1. Server name and id.
2. Endpoint used.
3. Test stage/status.
4. Tool count.
5. Explicit next step.
