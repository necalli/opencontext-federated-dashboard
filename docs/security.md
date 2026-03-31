# Security Guide

This repository is designed to be public-safe when configured correctly.

## Required Rules

1. Do not commit `.env`, `.env.local`, logs, or runtime data JSON.
2. Keep API keys and tokens in environment variables only.
3. Use placeholder hostnames in docs and examples.
4. Rotate keys immediately if exposure is suspected.

## Secret Handling

1. `ANTHROPIC_API_KEY` must come from environment at runtime.
2. Any MCP auth headers must be set through environment-backed config.
3. Never store real bearer tokens in tracked JSON or markdown.

## GitHub Controls

1. Enable Secret Scanning and Push Protection.
2. Protect `main` with pull-request review.
3. Add CI checks for tests and build on each PR.
4. Add automated secret scan (for example, gitleaks) in CI.

## Public Deployment Baseline

1. Disable debug mode in production.
2. Restrict CORS to known frontend origins.
3. Add backend authentication and rate limiting before internet exposure.
4. Redact or omit secret header values from API responses and logs.
