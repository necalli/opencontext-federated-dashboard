# Security Policy

## Supported Versions

Security fixes are applied to the latest `main` branch.

## Reporting a Vulnerability

1. Do not open public issues for suspected vulnerabilities.
2. Report security issues privately to the repository maintainers.
3. Include reproduction steps, impact assessment, and affected files/endpoints.
4. If credentials may have been exposed, rotate keys immediately.

## Security Expectations for Contributions

1. Never commit real API keys, tokens, or secrets.
2. Keep sensitive values in environment variables only.
3. Avoid logging secrets or auth headers.
4. Preserve secure defaults for CORS and debug settings.
