# Local Setup

This guide runs the dashboard fully on your own machine (no ngrok required).

## Prerequisites

1. Python 3.10+
2. Node.js 20+ and npm
3. Git
4. Optional: an `ANTHROPIC_API_KEY` for live model runtime

## 1) Backend

```bash
cd backend
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
copy ..\.env.example .env
python app.py
```

Backend health check:

```bash
curl http://127.0.0.1:5100/health
```

## 2) Frontend

```bash
cd frontend
npm install
# create frontend/.env.local with this value:
# VITE_API_BASE_URL=http://127.0.0.1:5100
npm run dev -- --host 0.0.0.0 --port 3000
```

Open: `http://localhost:3000`

## 3) Optional MCP providers (local)

1. OpenContext local MCP: run on `http://127.0.0.1:8000/mcp`
2. OpenGov NYC bridge: run on `http://127.0.0.1:8100/mcp`
3. OpenGov NYS bridge: run on `http://127.0.0.1:8101/mcp`

If you need one MCP ingress, run the path gateway:

```bash
cd backend
set MCP_OPENCONTEXT_URL=http://127.0.0.1:8000/mcp
set MCP_OPENGOV_URL=http://127.0.0.1:8100/mcp
set MCP_OPENGOV_NYS_URL=http://127.0.0.1:8101/mcp
set MCP_GATEWAY_HOST=0.0.0.0
set MCP_GATEWAY_PORT=8200
python tools/mcp_path_gateway.py
```

Then use:

1. `http://127.0.0.1:8200/opencontext/mcp`
2. `http://127.0.0.1:8200/opengov/mcp`
3. `http://127.0.0.1:8200/opengov-nys/mcp`

## 4) Run tests

```bash
cd backend
python -m unittest tests.test_server_registry tests.test_mcp_client tests.test_agent_runtime -v
```
