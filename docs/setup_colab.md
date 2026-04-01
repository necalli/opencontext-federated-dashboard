# Colab Setup

This guide is for running the dashboard in Google Colab from the public GitHub repo.

## Recommended Bootstrap (Single URL)

Use this path as the default public-user setup. It is validated in Colab and works with free ngrok accounts.

### 1. Use the canonical one-cell bootstrap file

- Open [opencontext-repo-colab-single-url.txt](./opencontext-repo-colab-single-url.txt).
- Copy the full file contents into one Colab cell.
- Set only:
  - `NGROK_AUTHTOKEN` (required)
  - `ANTHROPIC_API_KEY` (optional; required only for Anthropic-backed chat/runtime features)

### 2. Keep OpenContext runtime config external to the repo clone

In Colab, write runtime config to:

- `/content/opencontext-runtime-config.yaml`

and start OpenContext with:

- `OPENCONTEXT_CONFIG=/content/opencontext-runtime-config.yaml`

Do not rely on `/content/OpenContext/config.yaml` in Colab. In some OpenContext clones this path can resolve as a broken symlink (for example to `examples/dc-arcgis/config.yaml`) and cause `FileNotFoundError` during bootstrap.

### 3. Use single-URL topology for public/free ngrok usage

- Tunnel only frontend (`localhost:3000`) to a public URL.
- Keep backend (`:5100`) and MCP gateway (`:8200`) local inside Colab.
- Use Vite proxy so frontend routes `/api` and `/health` to local backend.
- Set `VITE_API_BASE_URL` to the frontend public URL.

This avoids multi-tunnel constraints while keeping the full MCP path active (OpenContext + OpenGov NYC + OpenGov NYS).

## Service Order

1. OpenContext MCP server (`:8000`)
2. OpenGov NYC bridge (`:8100`)
3. OpenGov NYS bridge (`:8101`)
4. MCP path gateway (`:8200`)
5. Dashboard backend (`:5100`)
6. Frontend (`:3000`)

## Expected Ready-State Checks

After bootstrap completes, verify:

1. Colab output shows `Registered MCP servers: 3`.
2. Dashboard Overview shows `3/3 enabled`.
3. Agent chat shows an active runtime (`agent_sdk` or Anthropic runtime, depending on keys/config).

## Notes

1. Never commit real keys/tokens.
2. Keep hostnames and secrets runtime-only.
3. Prefer `MCP_DEFAULT_SERVERS_JSON` for multi-server auto-registration.
