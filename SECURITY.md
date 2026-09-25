# Security Policy

## Supported Versions

| Version | Status | Supported |
| :--- | :--- | :--- |
| Current source | Pre-release preview | Best effort |

---

## Reporting a Vulnerability

We take the security and integrity of CAD Copilot seriously. If you discover a potential security vulnerability, please report it responsibly:

### How to Report

- **Do NOT create a public issue** for undisclosed security vulnerabilities.
- Before public release, the maintainer will enable and verify **Report a vulnerability** on the repository's [Security page](https://github.com/jitendra-patwari/cad-copilot/security). Do not include exploit details in a public issue.
- Please include reproducible steps, proof-of-concept payloads or configurations (if applicable), and an assessment of the security impact.

### Response Process

- As an independent open-source project, reports are reviewed and prioritized on a best-effort basis without a commercial service-level agreement (SLA).
- Validated vulnerabilities will be assessed for a fix and, where appropriate, a GitHub Security Advisory.

---

## Secret Handling & Local-First Isolation

CAD Copilot is engineered with a **local-first, zero-telemetry architecture**:

- **Session-Only API Keys:** The desktop app takes a Google Gemini API key through its in-memory Generate form. It does not persist the key to disk or include it in run manifests or diagnostics.
- **Optional Network Use:** CAD Copilot sends a text request to Google Gemini over HTTPS only when the user initiates prompt generation. Deterministic example generation and batch export do not use the Gemini service.
- **Portable CI Isolation:** Automated GitHub Actions CI workflows use zero repository secrets and run only portable, offline unit and integration quality gates.
- **Filesystem Containment:** Desktop file pickers enforce bounded output directories, prevent directory traversal attacks, and reject Windows reserved device names.
