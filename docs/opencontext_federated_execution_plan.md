# OpenContext Federated Dashboard Execution Plan

Created: March 18, 2026  
Owner: Product + Engineering

## 1) Purpose

Define the original design scope and execution plan for a new dashboard repo that:

1. Reuses proven agent architecture and UX patterns from `rental-dashboard`.
2. Integrates Anthropic MCP connector patterns.
3. Uses OpenContext as a federated backend model:
   one focused OpenContext server per plugin/data domain, with one intelligent orchestrator/dashboard on top.

---

## 2) Grounded Constraints (From Sources)

1. OpenContext enforces exactly one enabled plugin per deployment.
   - Source: `OpenContext/core/validators.py`, `OpenContext/docs/ARCHITECTURE.md`
2. OpenContext MCP contract is JSON-RPC over `/mcp` with `initialize`, `tools/list`, `tools/call`.
   - Source: `OpenContext/core/mcp_server.py`, `OpenContext/server/http_handler.py`
3. OpenContext CKAN plugin includes search/query/schema + advanced SQL/aggregate tools, with SQL safety checks.
   - Source: `OpenContext/plugins/ckan/plugin.py`, `OpenContext/plugins/ckan/sql_validator.py`
4. Current `rental-dashboard` already has:
   - runtime switching and deterministic fallback,
   - SSE chat streaming and tool-progress event rendering,
   - skill packaging/routing patterns.
   - Source: `rental-dashboard/backend/services/agent_chat_runtime.py`, `rental-dashboard/backend/app.py`, `rental-dashboard/frontend/src/components/AgentChat.jsx`, `rental-dashboard/backend/services/agent_skills.py`
5. Anthropic MCP connector supports direct remote MCP usage from Messages API and should be first-class in runtime design.
   - Source: Anthropic docs: https://platform.claude.com/docs/en/agents-and-tools/mcp-connector

---

## 3) Target Architecture (Federated)

## 3.1 High-Level

1. Multiple OpenContext servers (each with one plugin enabled).
2. One new dashboard/orchestrator repo that:
   - stores a registry of MCP servers,
   - aggregates tool catalogs,
   - routes agent tool calls to the correct server,
   - provides a unified chat + operations UI.

## 3.2 Runtime Strategy

1. Primary runtime: Anthropic Messages API with MCP connector.
2. Fallback runtime: deterministic orchestrator with direct MCP JSON-RPC calls.
3. Shared capabilities:
   - session history,
   - tool-call telemetry,
   - typed error normalization,
   - SSE streaming to frontend.

## 3.3 UX Strategy

1. Reuse key UX patterns from current dashboard:
   - floating/resizable chat drawer,
   - robust settings modal,
   - operations side panel,
   - mobile-aware layout.
2. Add MCP-specific surfaces:
   - server registry,
   - tool explorer/runner,
   - run timeline/trace inspector.

---

## 4) Scope

## 4.1 MVP In-Scope

1. Server registry (CRUD + connection test).
2. Unified tool listing from one or more OpenContext servers.
3. Chat workflow with MCP tool usage (streamed).
4. Deterministic fallback if MCP connector path fails.
5. Run logging and trace inspection.
6. CKAN workflow support:
   - dataset search,
   - dataset details,
   - resource query,
   - schema lookup,
   - SQL and aggregate tool support.

## 4.2 Out of Scope (Initial MVP)

1. Multi-tenant auth/SSO.
2. Full role-based permissioning.
3. Arbitrary non-OpenContext MCP server adapters.
4. Deep analytics/report builder.

---

## 5) New Repo Scaffold (Target)

Recommended repo name: `opencontext-agent-dashboard`

```text
opencontext-agent-dashboard/
  backend/
    app.py
    requirements.txt
    services/
      agent_runtime.py
      agent_orchestrator.py
      anthropic_mcp_connector.py
      opencontext_mcp_client.py
      skill_packages.py
      storage.py
      tool_router.py
      server_registry.py
    tests/
      test_agent_runtime.py
      test_mcp_client.py
      test_chat_api.py
      test_server_registry.py
  frontend/
    src/
      App.jsx
      components/
        AgentChat.jsx
        mcp/ServerRegistry.jsx
        mcp/ToolExplorer.jsx
        mcp/RunTimeline.jsx
      lib/
        session.js
  docs/
    architecture.md
    execution_plan.md
    deployment.md
```

---

## 6) Actionable Ticket Backlog

## P0 (Must Have for Initial Usable Build)

### P0-01: Repository bootstrap
Status: Completed (March 19, 2026)  
Goal: Create base repo with backend/frontend/docs/test scaffolding.

Implementation files:
1. `backend/app.py`
2. `backend/requirements.txt`
3. `frontend/package.json`
4. `frontend/src/App.jsx`
5. `docs/architecture.md`

Acceptance criteria:
1. `backend` starts and serves `/health`.
2. `frontend` starts and renders base shell.
3. CI/local test command executes without missing-module failures.

---

### P0-02: MCP server registry + connectivity checks
Status: Completed (March 19, 2026)  
Goal: Persist MCP server configs and verify endpoint health/handshake.

Implementation files:
1. `backend/services/server_registry.py`
2. `backend/services/storage.py`
3. `backend/app.py` (`/api/v1/mcp/servers/*`)
4. `frontend/src/components/mcp/ServerRegistry.jsx`

Acceptance criteria:
1. User can add/edit/delete MCP server entries.
2. “Test connection” validates `ping` + `initialize` + `tools/list`.
3. Failed checks return normalized user-facing diagnostics.

---

### P0-03: Direct OpenContext MCP client (deterministic path)
Status: Completed (March 19, 2026)  
Goal: Implement robust JSON-RPC client for OpenContext `/mcp`.

Implementation files:
1. `backend/services/opencontext_mcp_client.py`
2. `backend/tests/test_mcp_client.py`

Acceptance criteria:
1. Supports `initialize`, `tools/list`, `tools/call`.
2. Handles HTTP errors/timeouts/retries with typed error payloads.
3. Unit tests cover success, timeout, malformed response, and non-200.

---

### P0-04: Anthropic MCP connector runtime integration
Status: Completed (March 19, 2026)  
Goal: Add primary model runtime with MCP connector, with fallback.

Implementation files:
1. `backend/services/anthropic_mcp_connector.py`
2. `backend/services/agent_runtime.py`
3. `backend/services/agent_orchestrator.py`
4. `backend/tests/test_agent_runtime.py`

Acceptance criteria:
1. Runtime can use configured MCP server(s) through Anthropic connector.
2. Runtime falls back to deterministic MCP path when connector fails.
3. Debug metadata clearly shows active runtime and fallback reason.

---

### P0-05: SSE chat API + frontend streaming chat
Status: Completed (March 19, 2026)  
Goal: Recreate robust streamed chat behavior from rental dashboard.

Implementation files:
1. `backend/app.py` (`/api/v1/agent/chat`, `/api/v1/agent/chat/stream`)
2. `frontend/src/components/AgentChat.jsx`
3. `frontend/src/lib/session.js`

Acceptance criteria:
1. Frontend receives streamed deltas and final `done` response.
2. Tool progress events render during execution.
3. Non-stream fallback path works if stream fails.

---

### P0-06: Tool explorer (read + run)
Status: Completed (March 19, 2026)  
Goal: Provide operational UI for browsing tools and running calls.

Implementation files:
1. `frontend/src/components/mcp/ToolExplorer.jsx`
2. `backend/app.py` (`/api/v1/mcp/tools/list`, `/api/v1/mcp/tools/call`)

Acceptance criteria:
1. Tool catalog can be filtered by server.
2. User can execute tool with JSON args and see structured result.
3. Errors are displayed with actionable detail (not generic failure).

---

## P1 (High Value Enhancements)

### P1-01: Skill packages and routing for civic workflows
Status: Completed (March 19, 2026)  
Goal: Add skill scoping/routing similar to rental model.

Implementation files:
1. `backend/services/skill_packages.py`
2. `backend/agent_skills/*/SKILL.md`
3. `backend/services/agent_runtime.py`

Acceptance criteria:
1. Skills activate by keyword/intent.
2. Available tools are scoped by selected skills.
3. Routing debug metadata includes selected skill IDs.

---

### P1-02: Federated tool router across multiple MCP servers
Status: Completed (March 19, 2026)  
Goal: Route calls by server capability/domain instead of single endpoint.

Implementation files:
1. `backend/services/tool_router.py`
2. `backend/services/opencontext_mcp_client.py`
3. `backend/tests/test_tool_router.py`

Acceptance criteria:
1. Multiple registered servers can be active in one chat session.
2. Router picks intended server based on tool namespace/capability.
3. Cross-server failures degrade gracefully with clear remediation.

---

### P1-03: Run timeline + trace inspector UI
Status: Completed (March 19, 2026)  
Goal: Add transparent observability of execution behavior.

Implementation files:
1. `frontend/src/components/mcp/RunTimeline.jsx`
2. `backend/services/storage.py` (trace tables)
3. `backend/app.py` (`/api/v1/runs/*`)

Acceptance criteria:
1. Each chat run stores request, tool calls, durations, and errors.
2. UI can inspect a run and replay sequence details.
3. Export JSON for run diagnostics.

---

### P1-04: SQL safety UX guardrails
Status: Completed (March 19, 2026)  
Goal: Prevent accidental misuse of advanced SQL tools.

Implementation files:
1. `frontend/src/components/mcp/ToolExplorer.jsx` (advanced mode + warnings)
2. `backend/services/tool_router.py` (policy checks)

Acceptance criteria:
1. `execute_sql` requires explicit “advanced mode” confirmation in UI.
2. Query preview + warning shown before execution.
3. Timeout/row-limit guardrails are applied server-side.

---

### P1-05: Anthropic Agent SDK conformance runtime
Status: Completed (March 19, 2026)  
Goal: Make Anthropic Agent SDK the primary orchestration runtime with MCP tool execution and fallbacks.

Implementation files:
1. `backend/services/anthropic_agent_sdk_runtime.py`
2. `backend/services/agent_runtime.py`
3. `backend/services/agent_orchestrator.py`
4. `backend/app.py`
5. `backend/tests/test_agent_runtime.py`

Acceptance criteria:
1. Primary runtime is Agent SDK (`anthropic_agent_sdk`) with explicit runtime ordering.
2. Connector runtime remains available as secondary fallback path.
3. Deterministic direct-MCP runtime remains final fallback with normalized reason metadata.
4. SSE tool progress includes events from Agent SDK and connector paths.

---

### P1-06: Filesystem-first skills conformance
Status: Completed (March 19, 2026)  
Goal: Use skill artifacts (`SKILL.md` + `runtime.json`) as source of truth for routing and scope.

Implementation files:
1. `backend/services/skill_packages.py`
2. `backend/agent_skills/*/SKILL.md`
3. `backend/agent_skills/*/runtime.json`
4. `backend/tests/test_skill_packages.py`

Acceptance criteria:
1. Skills are loaded from filesystem packages instead of hardcoded Python maps.
2. Trigger keywords and tool scopes come from skill package metadata.
3. Runtime debug metadata includes selected skill packages and scoped tools.

---

### P1-07: UI/UX shell modernization and chat drawer
Status: Completed (March 20, 2026)  
Goal: Move from stacked utility sections to a modern operator shell with dark-default theming and drawer-based chat UX.

Implementation files:
1. `frontend/src/App.jsx`
2. `frontend/src/components/AgentChat.jsx`
3. `frontend/src/styles.css`
4. `frontend/src/components/mcp/RunTimeline.jsx`

Acceptance criteria:
1. Collapsible left rail hosts core workspace navigation and settings.
2. Theme toggle supports light/dark modes with dark as default.
3. Agent chat is a floating, resizable drawer with SSE streaming and fallback behavior preserved.
4. Core MCP registry/tool explorer/run timeline functionality is unchanged.

---

### P1-08: External Socrata MCP bridge integration
Status: Completed (March 20, 2026)  
Goal: Support adding stdio-only Socrata MCP servers (for example OpenGov/NYC) into the HTTP registry workflow.

Implementation files:
1. `backend/services/server_registry.py`
2. `backend/tests/test_server_registry.py`
3. `.env.example`
4. `README.md`
5. `docs/opengov_socrata_integration.md`

Acceptance criteria:
1. Backend supports multi-default server auto-registration via `MCP_DEFAULT_SERVERS_JSON`.
2. OpenContext + external Socrata bridge endpoints can auto-load together at startup.
3. Colab runbook exists for standing up OpenGov stdio bridge, tunneling, and testing registry connectivity.

---

## P2 (Scale + Production Readiness)

### P2-01: Authn/Authz and secret management
Status: Pending  
Goal: Add secure handling for API keys/tokens and role boundaries.

Implementation files:
1. `backend/services/server_registry.py` (secret references)
2. `backend/app.py` (auth middleware)
3. deployment config files

Acceptance criteria:
1. Server tokens are not stored in plaintext logs.
2. Protected operations require authenticated user context.
3. Security checks pass for baseline threat model.

---

### P2-02: Advanced reliability (retry budgets, circuit breaking)
Status: Pending  
Goal: Improve resilience across flaky upstream MCP endpoints.

Implementation files:
1. `backend/services/opencontext_mcp_client.py`
2. `backend/services/agent_runtime.py`

Acceptance criteria:
1. Per-server retry policy and cooldown are configurable.
2. Circuit breaker opens after repeated failures and auto-recovers.
3. User gets clear degraded-state messaging.

---

### P2-03: Deployment templates and operational runbooks
Status: Pending  
Goal: Productionize deployment/operations for the new dashboard.

Implementation files:
1. `docs/deployment.md`
2. `docs/runbook.md`
3. infra templates

Acceptance criteria:
1. Staging deploy path documented and reproducible.
2. Operational runbook covers outage triage and rollback.
3. On-call checklist can be executed without tribal knowledge.

---

## 7) File-by-File Implementation Order

Use this exact build order to reduce rework:

1. `backend/services/storage.py`
2. `backend/services/server_registry.py`
3. `backend/services/opencontext_mcp_client.py`
4. `backend/services/tool_router.py`
5. `backend/services/anthropic_mcp_connector.py`
6. `backend/services/skill_packages.py`
7. `backend/services/anthropic_agent_sdk_runtime.py`
8. `backend/services/agent_orchestrator.py`
9. `backend/services/agent_runtime.py`
10. `backend/app.py`
11. `backend/tests/test_mcp_client.py`
12. `backend/tests/test_server_registry.py`
13. `backend/tests/test_agent_runtime.py`
14. `backend/tests/test_chat_api.py`
15. `frontend/src/lib/session.js`
16. `frontend/src/components/AgentChat.jsx`
17. `frontend/src/components/mcp/ServerRegistry.jsx`
18. `frontend/src/components/mcp/ToolExplorer.jsx`
19. `frontend/src/components/mcp/RunTimeline.jsx`
20. `frontend/src/App.jsx`
21. `docs/architecture.md`
22. `docs/deployment.md`
23. `docs/runbook.md`

---

## 8) Milestone Gates

### Gate A: P0 complete
1. Multi-server registry works.
2. Chat can stream, call MCP tools, and fallback deterministically.
3. Tool explorer is usable for CKAN workflows.

### Gate B: P1 complete
1. Skill routing is stable.
2. Federated routing across servers is production-credible.
3. Agent SDK is primary runtime and fallback hierarchy is explicit.
4. Run traces provide sufficient debugging visibility.

### Gate C: P2 complete
1. Security and operational controls are in place.
2. Deployment and runbook are ready for sustained use.

---

## 9) Source Reference Index

Primary references used for this plan:

1. Anthropic MCP connector docs:  
   https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
2. Anthropic Agent SDK skills docs:  
   https://platform.claude.com/docs/en/agent-sdk/skills
3. Anthropic Agent SDK MCP docs:  
   https://platform.claude.com/docs/en/agent-sdk/mcp
4. OpenContext architecture and enforcement:
   - `OpenContext/docs/ARCHITECTURE.md`
   - `OpenContext/core/validators.py`
   - `OpenContext/core/plugin_manager.py`
5. OpenContext MCP protocol and endpoint behavior:
   - `OpenContext/core/mcp_server.py`
   - `OpenContext/server/http_handler.py`
6. OpenContext CKAN capabilities and SQL guardrails:
   - `OpenContext/plugins/ckan/plugin.py`
   - `OpenContext/plugins/ckan/sql_validator.py`
7. Rental dashboard reusable architecture and UI patterns:
   - `rental-dashboard/backend/services/agent_chat_runtime.py`
   - `rental-dashboard/backend/services/agent_skills.py`
   - `rental-dashboard/backend/app.py`
   - `rental-dashboard/frontend/src/components/AgentChat.jsx`
   - `rental-dashboard/frontend/src/App.jsx`
