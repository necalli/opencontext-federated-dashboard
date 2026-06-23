# Discovery Sources

Use the official MCP Registry as the sole discovery source.

## 1) Official MCP Registry

- Base: `https://registry.modelcontextprotocol.io`
- Try:
  - `/v0.1/servers?q=<topic>&limit=<n>`
  - `/v0.1/servers?query=<topic>&limit=<n>`
  - `/v0.1/servers` (client-side filter)
  - For each candidate server name, resolve details from:
    - `/v0.1/servers/{name}/versions/latest`
    - fallback `/v0.1/servers/{name}/versions`

Rationale: community-governed and aligned with MCP ecosystem defaults.
Important: many entries are `stdio` local-process transports; treat those as discovery-only unless you run a bridge.
For stdio-only servers, generate a bridge plan with `mcp_stdio_bridge_plan` and onboard the resulting local HTTP endpoint.

### Query Optimization

The MCP registry search tokenizes on individual keywords. Long composite phrases reduce match quality significantly. Follow these rules:

- Prefer single-word queries: `reddit`, `weather`, `census`, `FDA`, `NASA`, `SEC`
- Two-word queries are acceptable only when a single word is genuinely too broad: `stock market`, `open FDA`
- Never use 3+ word queries — they do not improve results and often return fewer matches
- If a query returns 0 results, do NOT expand it. Instead retry with:
  1. A synonym (e.g. `weather` → `NOAA` → `climate`)
  2. A related specific term (e.g. `federal` → `government` → `agency`)
  3. The vendor/brand name directly (e.g. `semaglutide` → `Ozempic` → `GLP`)
- Run each retry as a separate `mcp_server_discover` call, one word at a time

## Recommendation Rules

1. Prefer already-enabled local servers when they match topic intent.
2. Prefer candidates with explicit remote HTTP/SSE MCP endpoint URL.
3. Include auth determination and verification score in each recommendation.
4. Do not onboard until user confirms.
