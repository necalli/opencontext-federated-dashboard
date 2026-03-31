# Post-Launch Hardening Checklist

Use this checklist to track security and operational hardening after public launch.

## P0: Immediate (this week)

- [ ] Enable GitHub Secret Scanning and Push Protection.
- [ ] Confirm branch protection/ruleset is active for `main`.
- [ ] Require PRs and required checks (`backend-tests`, `frontend-build`) on `main`.
- [ ] Disable force-pushes and branch deletions on `main`.
- [ ] Verify no sensitive values are exposed in API responses (`headers` redaction check).
- [ ] Verify production runtime uses `BACKEND_DEBUG=false`.
- [ ] Restrict `BACKEND_CORS_ORIGINS` to trusted frontend hosts only.
- [ ] Add and verify repository `SECURITY.md`, `LICENSE`, and `CONTRIBUTING.md` visibility.

## P1: Near-Term (next 2-3 weeks)

- [ ] Add backend authentication for protected endpoints.
- [ ] Add backend rate limiting for chat/tool endpoints.
- [ ] Add automated secret scanning in CI (for example, gitleaks).
- [ ] Add dependency update policy and review cadence.
- [ ] Add issue templates for bug/security/reporting workflows.
- [ ] Add PR template requiring test results and security impact notes.
- [ ] Create `v0.1.0` release notes and changelog baseline.

## P2: Reliability and Operations

- [ ] Add staging environment and deployment validation checklist.
- [ ] Add runbook for incident response and rollback.
- [ ] Add structured logging policy (with secret redaction checks).
- [ ] Define backup/retention strategy for runtime data stores.
- [ ] Add health/SLO dashboard metrics for backend and key endpoints.

## Frontend Build Follow-Ups

- [ ] Investigate large Vite chunk warning (>500 kB).
- [ ] Evaluate code splitting for heavy visualization modules.
- [ ] Set target bundle-size thresholds for CI alerts.

## Tracking

- [ ] Create GitHub issues for each unchecked item and link them here.
- [ ] Assign owner and due date to each issue.
- [ ] Review checklist weekly until all P0/P1 items are complete.
