# Discovery Sources

Use this priority order for MCP discovery:

1. Official MCP Registry
2. MCP Market (supplementary with page-level vetting)

## 1) Official MCP Registry

- Base: `https://registry.modelcontextprotocol.io`
- Try:
  - `/v0/servers?query=<topic>&limit=<n>`
  - `/v0/servers?q=<topic>&limit=<n>`
  - `/v0/servers` (client-side filter)

Rationale: community-governed and aligned with MCP ecosystem defaults.

## 2) MCP Market

- Base: `https://mcpmarket.com`
- Discovery pages:
  - `/search/<topic>`
  - `/search?q=<topic>`
- Candidate pages:
  - `/server/<slug>`

Vetting expectations:

- Extract and validate an MCP endpoint candidate (`https://.../mcp` preferred).
- Identify auth hints (`no_auth_required`, `auth_required`, or `unknown`).
- Cross-check linked homepage/repository (for example GitHub) before recommending.
- Treat MCP Market results as lower trust than official registry entries.

## Recommendation Rules

1. Prefer already-enabled local servers when they match topic intent.
2. Prefer candidates with explicit HTTP MCP endpoint URL.
3. Prefer official registry candidates over third-party listings.
4. Include auth determination and verification score in each recommendation.
5. Do not onboard until user confirms.
