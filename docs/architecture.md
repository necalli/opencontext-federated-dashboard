# Architecture Baseline

## Goal

Build a domain-neutral dashboard that federates multiple OpenContext MCP servers under one orchestrator and one operator UI.

## Constraints

1. OpenContext supports one enabled plugin per deployment.
2. MCP contract uses JSON-RPC methods: `initialize`, `tools/list`, `tools/call`.
3. Anthropic MCP connector is primary runtime path; direct MCP client is fallback.

## Baseline Components

1. `backend/app.py`: Flask API shell, health endpoint, scaffold chat endpoints (stream + non-stream).
2. `backend/services/`: placeholder package for registry, MCP client, runtime modules.
3. `frontend/src/App.jsx`: responsive shell for registry/catalog/orchestrator sections.
4. `docs/opencontext_federated_execution_plan.md`: ticketed source-of-truth roadmap.

## Next Build Slice

1. Implement persisted server registry + connectivity tests.
2. Add deterministic OpenContext MCP client with typed errors.
3. Wire chat runtime to Anthropic MCP connector + fallback orchestration.
