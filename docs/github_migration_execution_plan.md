# OpenContext Dashboard GitHub Migration Execution Plan

Created: March 30, 2026  
Owner: You (Product/Engineering)  
Scope: Publish a clean, public, runnable GitHub repo while preserving this current repo as a private/personal working copy.

## 1) Outcomes

By the end of this plan:

1. A separate, clean public repo exists.
2. No personal secrets, local paths, or sensitive logs are published.
3. New users can run the dashboard locally using documented steps.
4. Security baseline is in place for public exposure.

---

## 2) Current Risks to Address First

Priority findings from current code/docs:

1. `backend/services/server_registry.py` currently returns full `headers` in public records (`_public_record`), which can expose auth values if stored.
2. `backend/app.py` enables unrestricted CORS (`CORS(app)`) and forces Flask debug mode on startup (`debug=True`).
3. `docs/Colab Flow.txt` contains personal runtime patterns/hostnames and should be sanitized for public docs.
4. Root includes personal chat logs (`chatlog_*.txt`) that should not be in a public release.
5. README frontend default port should be reconciled with `frontend/vite.config.js` (`3000`).

---

## 3) Migration Strategy (Keep Personal Repo Unchanged)

Use a **two-repo approach**:

1. Personal repo (current): stays as-is for private experiments.
2. Public repo (new sibling directory): clean copy with hardened defaults + public documentation.

This prevents accidental leakage from your personal workspace/history.

---

## 4) Phase Plan

## Phase A: Create Clean Public Working Copy (Day 0)

Run from PowerShell (outside the current repo if possible):

```powershell
$PERSONAL = "C:\path\to\opencontext-federated-dashboard"
$PUBLIC   = "C:\path\to\opencontext-federated-dashboard-public"

New-Item -ItemType Directory -Force $PUBLIC | Out-Null

robocopy $PERSONAL $PUBLIC /E `
  /XD .git node_modules dist .venv venv backend\data `
  /XF .env .env.local *.log chatlog_*.txt .codex_write_probe
```

Expected result:

1. Public folder has source/docs/config templates only.
2. No git history copied from personal repo.
3. No local runtime data copied (`backend/data`).

---

## Phase B: Public Repo Hardening (Day 0-1)

In the new public folder:

1. Update `.gitignore` to explicitly exclude:
   - `chatlog_*.txt`
   - `.codex_write_probe`
   - `backend/data/*.json` (keep `.gitkeep`)
2. Sanitize `docs/Colab Flow.txt`:
   - Replace all personal hostnames and local paths with placeholders.
   - Keep only template values.
3. Update `README.md`:
   - Align frontend port docs with actual config (`3000`) or change Vite config to documented value.
   - Add a local-only quickstart path (no ngrok required).
4. Add/expand docs:
   - `docs/setup_local.md`
   - `docs/setup_colab.md`
   - `docs/security.md`
   - `docs/troubleshooting.md`

Acceptance criteria:

1. A first-time user can run backend + frontend locally from README/docs alone.
2. No doc contains personal URLs, personal directories, or real tokens.

---

## Phase C: Code Security Fixes (Day 1)

Implement these code changes in public repo:

1. `backend/services/server_registry.py`
   - In `_public_record`, do not return raw `headers`.
   - Return only `header_keys` (or masked values).
2. `backend/app.py`
   - Make CORS origins env-driven (allowlist), not open by default.
   - Make debug mode env-driven and default to `False`.
3. Add production-safe env variables to `.env.example`:
   - `BACKEND_DEBUG=false`
   - `BACKEND_CORS_ORIGINS=http://localhost:3000`
4. Add tests for header-redaction behavior and any config changes.

Acceptance criteria:

1. API responses never expose secret header values.
2. Production run does not use Flask debug mode.
3. CORS is restricted by default.

---

## Phase D: Publish Readiness Validation (Day 1-2)

Run in the public repo:

```powershell
cd backend
python -m unittest tests.test_server_registry tests.test_mcp_client tests.test_agent_runtime -v

cd ..\frontend
npm install
npm run build
```

Then perform a clean-clone validation on a separate machine/account:

1. Clone repo.
2. Copy `.env.example` to `.env` and set required values.
3. Run local stack end-to-end.
4. Verify all core capabilities:
   - server registry CRUD + connection tests
   - tool listing/routing across OpenContext + OpenGov
   - SSE chat streaming
   - skill activation/routing
   - visualization publishing to canvas

---

## Phase E: GitHub Publication (Day 2)

In public repo:

```powershell
git init -b main
git add .
git commit -m "Initial public release: OpenContext federated dashboard"
git remote add origin <your-github-repo-url>
git push -u origin main
```

Immediately configure GitHub repository settings:

1. Enable Secret Scanning and Push Protection.
2. Add branch protection on `main` (PR required).
3. Add `SECURITY.md`, `LICENSE`, `CONTRIBUTING.md`.
4. Add GitHub Actions CI for backend tests + frontend build.

---

## 5) Work Breakdown (Execution Backlog)

P0 (must complete before public push):

1. Create clean public working copy.
2. Remove/sanitize personal artifacts and docs.
3. Fix header exposure in server registry API.
4. Lock CORS and debug defaults.
5. Validate local runbook from clean clone.

P1 (strongly recommended right after launch):

1. Add auth/rate limiting for backend endpoints.
2. Add automated secret scan in CI.
3. Add release tags + changelog.
4. Add Docker-based local run option.

P2 (production maturity):

1. Replace plaintext runtime storage with managed DB.
2. Add structured audit logging + log redaction policy.
3. Add staging deployment + operational runbook.

---

## 6) Rollback/Safety

If any sensitive material is pushed accidentally:

1. Rotate affected key/token immediately.
2. Remove exposed values from code/docs.
3. Rewrite git history in public repo before re-publishing.
4. Re-scan repo with secret scanners.

---

## 7) Definition of Done

Migration is complete when all are true:

1. Public repo is separate from personal repo and contains no private artifacts.
2. No secret values are exposed in code, docs, runtime responses, or history.
3. A new user can run the dashboard end-to-end with provided documentation.
4. Baseline GitHub security controls are enabled.
5. P0 checklist is fully complete and verified.

