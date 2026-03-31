# Colab Setup

This guide is for running the dashboard in Google Colab with Google Drive mounted.

## Steps

1. Mount Drive and copy the repo to `/content/opencontext-dashboard_run`.
2. Configure required secrets in notebook cells:
   - `NGROK_AUTHTOKEN`
   - `ANTHROPIC_API_KEY` (optional)
3. Start local services in order:
   - OpenContext MCP server (`:8000`)
   - OpenGov NYC bridge (`:8100`)
   - OpenGov NYS bridge (`:8101`)
   - MCP path gateway (`:8200`)
   - Dashboard backend (`:5100`)
   - Frontend (`:3000`)
4. Create ngrok tunnels for gateway, backend, and frontend.
5. Verify health checks and MCP initialize/ping calls before chat runs.

## Reference Notebook Flow

Use [Colab Flow.txt](./Colab%20Flow.txt) as the sanitized template.

## Notes

1. Keep all hostnames as placeholders until runtime.
2. Never hardcode tokens in committed files.
3. Prefer `MCP_DEFAULT_SERVERS_JSON` for multi-server startup registration.
