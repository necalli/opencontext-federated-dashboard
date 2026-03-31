# OpenGov Socrata Integration (NYC + NYS/MTA)

This guide integrates `srobbin/opengov-mcp-server` with the OpenContext Federated Dashboard for:
- NYC Open Data (`https://data.cityofnewyork.us`)
- NYS Open Data (`https://data.ny.gov`, including MTA-published datasets)

## Why a bridge is required

`opengov-mcp-server` currently runs on **stdio** transport.  
This dashboard registry expects MCP servers reachable via **HTTP JSON-RPC** endpoint (`.../mcp`).

Use `mcp-http-bridge` to expose the stdio server as HTTP.

## Colab setup

Run these in order after your drive mount.

### 1) Install bridge + Node

```bash
%%bash
set -euo pipefail
pip install -q mcp-http-bridge
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
node -v && npm -v
```

### 2) Create bridge config (NYC Socrata portal)

```bash
%%bash
set -euo pipefail
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
cat /content/opengov-bridge.json
```

### 2b) Create bridge config (NYS Socrata portal, includes MTA data on data.ny.gov)

```bash
%%bash
set -euo pipefail
cat >/content/opengov-nys-bridge.json <<'JSON'
{
  "server": {
    "command": "npx",
    "args": ["-y", "opengov-mcp-server@latest"],
    "env": {
      "DATA_PORTAL_URL": "https://data.ny.gov"
    }
  }
}
JSON
cat /content/opengov-nys-bridge.json
```

### 3) Start HTTP bridge on port 8100

```bash
%%bash
set -euo pipefail
command -v node >/dev/null
command -v npx >/dev/null
node -v
npx -v
nohup python -m mcp_http_bridge.main \
  --config /content/opengov-bridge.json \
  --host 0.0.0.0 \
  --port 8100 \
  --path /mcp > /content/opengov-bridge.log 2>&1 &
```

### 3b) Start NYS HTTP bridge on port 8101

```bash
%%bash
set -euo pipefail
command -v node >/dev/null
command -v npx >/dev/null
node -v
npx -v
nohup python -m mcp_http_bridge.main \
  --config /content/opengov-nys-bridge.json \
  --host 0.0.0.0 \
  --port 8101 \
  --path /mcp > /content/opengov-nys-bridge.log 2>&1 &
```

### 4) Verify local MCP ping

```bash
%%bash
set -euo pipefail
ok=0
for i in $(seq 1 45); do
  if curl -fsS -X POST http://127.0.0.1:8100/mcp \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"colab-check","version":"0.1.0"},"capabilities":{"tools":{}}}}' >/tmp/opengov-ping.json 2>/tmp/opengov-ping.err; then
    ok=1
    break
  fi
  sleep 2
done

if [ "$ok" -ne 1 ]; then
  echo "OpenGov bridge did not become ready in time."
  echo "---- Bridge log tail ----"
  tail -n 120 /content/opengov-bridge.log || true
  echo "---- Last curl error ----"
  cat /tmp/opengov-ping.err || true
  exit 1
fi

python -m json.tool /tmp/opengov-ping.json
```

### 4b) Verify NYS local MCP initialize

```bash
%%bash
set -euo pipefail
ok=0
for i in $(seq 1 45); do
  if curl -fsS -X POST http://127.0.0.1:8101/mcp \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"colab-check","version":"0.1.0"},"capabilities":{"tools":{}}}}' >/tmp/opengov-nys-ping.json 2>/tmp/opengov-nys-ping.err; then
    ok=1
    break
  fi
  sleep 2
done

if [ "$ok" -ne 1 ]; then
  echo "OpenGov NYS bridge did not become ready in time."
  echo "---- Bridge log tail ----"
  tail -n 120 /content/opengov-nys-bridge.log || true
  echo "---- Last curl error ----"
  cat /tmp/opengov-nys-ping.err || true
  exit 1
fi

python -m json.tool /tmp/opengov-nys-ping.json
```

### 5) Expose with ngrok (dedicated endpoint mode)

```python
from pyngrok import ngrok
opengov_tunnel = ngrok.connect(8100, "http", hostname="your-opengov.ngrok.dev")
OPENGOV_MCP_URL = opengov_tunnel.public_url + "/mcp"
print("OpenGov MCP URL:", OPENGOV_MCP_URL)
```

### 6) Register in dashboard backend

```bash
%%bash
set -euo pipefail
BACKEND_URL="https://your-backend.ngrok.dev"   # change if needed
MCP_URL="https://your-opengov.ngrok.dev/mcp"   # replace with OPENGOV_MCP_URL value

curl -fsS -X POST "$BACKEND_URL/api/v1/mcp/servers" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"nyc-opengov\",\"endpoint\":\"$MCP_URL\",\"description\":\"Socrata NYC via OpenGov bridge\",\"enabled\":true}" | python -m json.tool

curl -fsS "$BACKEND_URL/api/v1/mcp/servers" | python -m json.tool
```

### 7) Test connection from dashboard API

Use the returned `server.id` from the previous step:

```bash
%%bash
set -euo pipefail
BACKEND_URL="https://your-backend.ngrok.dev"
SERVER_ID="replace-with-server-id"
curl -fsS -X POST "$BACKEND_URL/api/v1/mcp/servers/$SERVER_ID/test" | python -m json.tool
```

Expected result: `ping`, `initialize`, and `tools/list` all `ok: true`.

## Notes

1. `opengov-mcp-server` exposes a unified `get_data` tool (not CKAN tool names).
2. Use a separate ngrok hostname for OpenGov unless you have explicit path-based reverse proxy routing.
3. If the bridge dies, re-run steps 3-5 and update endpoint if hostname changed.

---

## 3-endpoint-safe mode (recommended for ngrok endpoint limits)

If your ngrok plan allows only 3 concurrent endpoints, use this pattern:

1. one tunnel for **MCP gateway** (all MCP servers behind one domain)
2. one tunnel for dashboard **backend**
3. one tunnel for dashboard **frontend**

### A) Keep local services on separate ports

- OpenContext local server: `http://127.0.0.1:8000/mcp`
- OpenGov NYC bridge: `http://127.0.0.1:8100/mcp`
- OpenGov NYS bridge: `http://127.0.0.1:8101/mcp`

### B) Start local path gateway (single MCP ingress)

This repo includes:
- `backend/tools/mcp_path_gateway.py`

Run it in Colab:

```bash
%%bash
set -euo pipefail
cd /content/opencontext-dashboard_run/backend
export MCP_OPENCONTEXT_URL="http://127.0.0.1:8000/mcp"
export MCP_OPENGOV_URL="http://127.0.0.1:8100/mcp"
export MCP_OPENGOV_NYS_URL="http://127.0.0.1:8101/mcp"
export MCP_GATEWAY_HOST="0.0.0.0"
export MCP_GATEWAY_PORT="8200"
nohup python tools/mcp_path_gateway.py > /content/mcp-gateway.log 2>&1 &
sleep 3
tail -n 60 /content/mcp-gateway.log || true
curl -sS http://127.0.0.1:8200/_gateway/health | python -m json.tool
```

### C) Expose only gateway port with ngrok

```python
from pyngrok import ngrok
mcp_gateway_tunnel = ngrok.connect(addr="8200", proto="http", hostname="your-mcp-gateway.ngrok.dev")
MCP_GATEWAY_URL = mcp_gateway_tunnel.public_url
print("MCP_GATEWAY_URL:", MCP_GATEWAY_URL)
print("OpenContext endpoint:", MCP_GATEWAY_URL + "/opencontext/mcp")
print("OpenGov NYC endpoint:", MCP_GATEWAY_URL + "/opengov/mcp")
print("OpenGov NYS endpoint:", MCP_GATEWAY_URL + "/opengov-nys/mcp")
```

### D) Verify all paths through one domain

```bash
%%bash
set -euo pipefail
MCP_GATEWAY_URL="https://your-mcp-gateway.ngrok.dev"

curl -sS -X POST "$MCP_GATEWAY_URL/opencontext/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":"1","method":"ping","params":{}}' | python -m json.tool

curl -sS -X POST "$MCP_GATEWAY_URL/opengov/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"colab-check","version":"0.1.0"},"capabilities":{"tools":{}}}}' | python -m json.tool

curl -sS -X POST "$MCP_GATEWAY_URL/opengov-nys/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"colab-check","version":"0.1.0"},"capabilities":{"tools":{}}}}' | python -m json.tool
```

### E) Auto-register all servers at backend startup

```python
import json, os
MCP_GATEWAY_URL = "https://your-mcp-gateway.ngrok.dev"
os.environ["MCP_DEFAULT_SERVER_AUTO_REGISTER"] = "true"
os.environ["MCP_DEFAULT_SERVERS_JSON"] = json.dumps([
    {
        "name": "opencontext-main",
        "endpoint": f"{MCP_GATEWAY_URL}/opencontext/mcp",
        "description": "OpenContext via gateway",
        "enabled": True
    },
    {
        "name": "nyc-opengov",
        "endpoint": f"{MCP_GATEWAY_URL}/opengov/mcp",
        "description": "OpenGov NYC via gateway",
        "enabled": True
    },
    {
        "name": "nys-opengov",
        "endpoint": f"{MCP_GATEWAY_URL}/opengov-nys/mcp",
        "description": "OpenGov NYS (incl MTA) via gateway",
        "enabled": True
    }
])
```

