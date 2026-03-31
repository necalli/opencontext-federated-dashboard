# Troubleshooting

## Backend will not start

1. Confirm Python version is 3.10+.
2. Reinstall dependencies:

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
```

3. Verify no other process is using port `5100`.

## Frontend cannot reach API

1. Confirm backend health: `http://127.0.0.1:5100/health`.
2. Confirm `frontend/.env.local` has `VITE_API_BASE_URL=http://127.0.0.1:5100`.
3. Restart Vite after updating env vars.

## MCP server test fails

1. Verify endpoint includes `/mcp`.
2. Check service locally first (`curl` ping/initialize).
3. Confirm ngrok tunnel is active and hostname matches registration.
4. Re-run backend with fresh `MCP_DEFAULT_SERVERS_JSON` if auto-registration is used.

## OpenGov bridge does not initialize

1. Confirm Node and npx are installed.
2. Confirm `DATA_PORTAL_URL` is valid (`https://data.cityofnewyork.us` or `https://data.ny.gov`).
3. Inspect bridge logs for startup errors.

## Streaming chat stuck or empty

1. Confirm backend `/api/v1/agent/chat/stream` is reachable.
2. Check browser devtools network events for SSE stream chunks.
3. If model key is missing, use deterministic mode for MCP-only testing.

## pip install fails with SSL certificate verify failed

1. This is usually an environment certificate chain issue (not a repo dependency issue).
2. Retry with trusted-host flags:

```bash
python -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt -r requirements-dev.txt
```

3. If policy requires internal package mirrors, use your organization-approved index URL instead.
