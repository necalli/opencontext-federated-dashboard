# Contributing

## Development Setup

1. Backend:

```bash
cd backend
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```

2. Frontend:

```bash
cd frontend
npm install
```

3. Environment:

```bash
copy .env.example .env
```

## Local Validation Before PR

1. Backend tests:

```bash
cd backend
python -m unittest tests.test_server_registry tests.test_app_config tests.test_mcp_client tests.test_agent_runtime -v
```

2. Frontend build:

```bash
cd frontend
npm run build
```

## Pull Request Guidelines

1. Keep changes scoped and documented.
2. Include tests for behavior changes.
3. Do not commit `.env`, logs, runtime data JSON, or local secrets.
4. Update docs when setup or runtime behavior changes.
