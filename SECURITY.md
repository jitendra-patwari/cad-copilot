# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

We take the security of CAD Copilot seriously. If you discover a potential security vulnerability, please report it responsibly.

### How to Report

- **Do NOT create a public GitHub issue** for undisclosed security vulnerabilities.
- Submit vulnerability reports via [GitHub Private Vulnerability Reporting](https://github.com/jitendra-patwari/cad-copilot/security/advisories/new).
- Include detailed steps to reproduce the vulnerability, proof-of-concept payloads (if applicable), and your assessment of the impact.

### Response Process

- We will acknowledge receipt of your vulnerability report within 48 hours.
- We will provide an initial assessment and timeline for remediation.
- Once a fix is developed and verified, a security advisory and patched release will be published.

## Secret Handling & Local-First Isolation

CAD Copilot is designed with a **100% local-first, zero-login architecture**:
- API keys (Google GenAI, OpenAI) supplied in local `.env` files or in-app settings are stored exclusively on the user's local machine and are never transmitted to external telemetry servers.
- When running in automated environments or CI/CD pipelines, ensure sensitive environment variables are populated using encrypted repository secrets.
