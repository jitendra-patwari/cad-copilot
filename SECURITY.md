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
- Gemini API keys are read from a local environment variable or session-only UI entry. CAD Copilot never persists them, logs them, or sends them to telemetry; they are provided only to Google Gemini when the user explicitly requests live AI generation.
- When running in automated environments or CI/CD pipelines, ensure sensitive environment variables are populated using encrypted repository secrets.
