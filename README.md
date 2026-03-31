# OpenContext Federated Dashboard

A domain-neutral dashboard/orchestrator that federates across many focused OpenContext MCP servers (one plugin per server) and exposes one unified operator + chat experience.

## Current Status

- `P0-01` complete: base backend/frontend scaffold.
- `P0-02` complete: MCP server registry + connection checks.
- `P0-03` complete: direct OpenContext JSON-RPC MCP client.
- `P0-04` complete: Anthropic MCP connector runtime with deterministic fallback + debug metadata.
- `P0-05` complete: SSE chat + tool progress rendering.
- `P0-06` complete: Tool Explorer list/run APIs and UI.
- `P1-03` complete: Run timeline + trace inspector APIs/UI.
- `P1-04` complete: SQL safety guardrails (advanced mode + LIMIT checks + timeout policy).
- `P1-05` complete: Anthropic Agent SDK conformance runtime (primary runtime path).
- `P1-06` complete: filesystem-first skill packages (`SKILL.md` + `runtime.json`).

Default runtime order is now:
1. `anthropic_agent_sdk`
2. `anthropic_mcp_connector`
3. `deterministic_mcp_fallback`

## Repo Layout

- `backend/` Flask API and orchestration runtime
- `frontend/` React + Vite operator UI
- `docs/` architecture and execution plan

## Quick Start

Detailed runbooks:
- [Local setup](docs/setup_local.md)
- [Colab setup](docs/setup_colab.md)
- [Security guide](docs/security.md)
- [Troubleshooting](docs/troubleshooting.md)

### Backend

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python app.py
```

Backend defaults to `http://127.0.0.1:5100`.

Runtime preference can be set per request on chat endpoints via:
- `runtime_preference: "agent_sdk"`
- `runtime_preference: "connector"`
- `runtime_preference: "deterministic"`

For strict MCP-only Agent SDK behavior, keep:
- `AGENT_SDK_SETTING_SOURCES=` (empty)
- `AGENT_SDK_NATIVE_SKILLS_ENABLED=false`
- `AGENT_SDK_DISALLOWED_TOOLS=...` (see `.env.example`)
- `AGENT_SDK_PROMPT_HISTORY_FALLBACK_ENABLED=false`

To enable auto-approval for selected built-ins:
- `AGENT_SDK_AUTO_APPROVE_BUILTINS_ENABLED=true`
- `AGENT_SDK_AUTO_APPROVE_BUILTINS=ToolSearch,AskUserQuestion,WebSearch`

Optional advanced override:
- `AGENT_SDK_AUTO_APPROVE_TOOLS=<comma-separated-tool-names>`

To auto-connect a default MCP endpoint on backend startup (so UI form entry is not required every run):
- `MCP_DEFAULT_SERVER_AUTO_REGISTER=true`
- `MCP_DEFAULT_SERVER_NAME=opencontext-main`
- `MCP_DEFAULT_SERVER_ENDPOINT=https://<your-ngrok-host>/mcp`
- `MCP_DEFAULT_SERVER_DESCRIPTION=OpenContext default MCP server`
- `MCP_DEFAULT_SERVER_ENABLED=true`
- optional auth headers JSON: `MCP_DEFAULT_SERVER_HEADERS_JSON={"Authorization":"Bearer <token>"}`
- optional multi-server bootstrap JSON array:
  `MCP_DEFAULT_SERVERS_JSON=[{"name":"opencontext-main","endpoint":"https://opencontext.ngrok.dev/mcp"},{"name":"nyc-opengov","endpoint":"https://opengov.ngrok.dev/mcp","description":"OpenGov Socrata NYC"}]`

### OpenGov (Socrata/NYC) Integration

The `srobbin/opengov-mcp-server` project is stdio-only. This dashboard expects HTTP MCP endpoints, so run a stdio-to-HTTP bridge and register the bridged URL.

Colab-ready flow:

1. Install bridge + Node:
```bash
pip install -q mcp-http-bridge
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
node -v && npm -v
```

2. Create OpenGov bridge config:
```bash
cat >/content/opengov-bridge.json <<'JSON'
{
  "server": {
    "command": "npx",
    "args": ["-y", "opengov-mcp-server@latest"],
    "env": {
      "DATA_PORTAL_URL": "https://data.cityofnewyork.us"
    }
  }
}
JSON
```

3. Start bridge on local port `8100`:
```bash
nohup python -m mcp_http_bridge.main \
  --config /content/opengov-bridge.json \
  --host 0.0.0.0 \
  --port 8100 \
  --path /mcp > /content/opengov-bridge.log 2>&1 &
```

4. Quick local ping:
```bash
curl -sS -X POST http://127.0.0.1:8100/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"ping","params":{}}' | python -m json.tool
```

5. Tunnel it (new hostname/domain recommended):
```python
from pyngrok import ngrok
opengov_tunnel = ngrok.connect(8100, "http", hostname="your-opengov.ngrok.dev")
print("OpenGov MCP:", opengov_tunnel.public_url + "/mcp")
```

6. Register in dashboard:
- Name: `nyc-opengov`
- Endpoint: `https://your-opengov.ngrok.dev/mcp`
- Description: `Socrata NYC via OpenGov bridge`

If you are constrained to 3 ngrok endpoints, use the included path gateway:
- run `backend/tools/mcp_path_gateway.py` on local port `8200`
- configure:
  - `MCP_OPENCONTEXT_URL=http://127.0.0.1:8000/mcp`
  - `MCP_OPENGOV_URL=http://127.0.0.1:8100/mcp`
- expose only `8200` and register:
  - `https://your-mcp-gateway.ngrok.dev/opencontext/mcp`
  - `https://your-mcp-gateway.ngrok.dev/opengov/mcp`

Or auto-register with env:
```bash
export MCP_DEFAULT_SERVERS_JSON='[{"name":"opencontext-main","endpoint":"https://your-opencontext.ngrok.dev/mcp"},{"name":"nyc-opengov","endpoint":"https://your-opengov.ngrok.dev/mcp","description":"Socrata NYC via OpenGov bridge"}]'
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend defaults to `http://localhost:3000`.

## Test

```bash
cd backend
python -m unittest tests.test_server_registry tests.test_mcp_client tests.test_agent_runtime -v
```


