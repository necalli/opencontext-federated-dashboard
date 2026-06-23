# Server Manifest Schema

Use this schema when preparing onboarding payloads.

```json
{
  "name": "string (required)",
  "endpoint": "string (required, should end with /mcp)",
  "description": "string",
  "enabled": "boolean (default true)",
  "headers": {
    "Header-Name": "literal-value"
  },
  "headers_env": {
    "Header-Name": "ENV_VAR_NAME"
  }
}
```

## Field Guidance

1. `name`
- Lowercase slug preferred.
- Include source intent if useful (`nyc-opengov`, `nys-opengov`, `opencontext-main`).

2. `endpoint`
- Must be reachable by backend runtime.
- Use HTTPS for public tunnels.
- Use local URL for same-network deployments.

3. `headers` vs `headers_env`
- Prefer `headers_env` for sensitive values.
- Use `headers` only for non-sensitive values.

4. `enabled`
- Use `true` for standard onboarding.
- Use `false` only when staging or pre-validating.

## Minimal Example

```json
{
  "name": "metro-opendata",
  "endpoint": "https://metro-data.example.dev/mcp",
  "description": "Metro transit data via MCP",
  "enabled": true
}
```

## Auth Example (Environment-backed)

```json
{
  "name": "private-catalog",
  "endpoint": "https://private-catalog.example.dev/mcp",
  "description": "Private MCP catalog",
  "enabled": true,
  "headers_env": {
    "Authorization": "PRIVATE_CATALOG_BEARER"
  }
}
```
