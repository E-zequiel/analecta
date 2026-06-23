# Security Policy

## Supported Versions

This project is maintained by a single developer. To keep security maintenance sustainable, only the **latest stable release** receives security patches.

| Version  | Supported          |
| -------- | ------------------- |
| Latest   | :white_check_mark: |
| Older    | :x:                 |

If you are running an older version, please upgrade to the latest release before reporting a vulnerability — the issue may already be fixed.

## Reporting a Vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

### Primary channel: GitHub Private Vulnerability Reporting

This repository has [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability) enabled. To report a vulnerability:

1. Go to the **Security** tab of this repository.
2. Click **Report a vulnerability**.
3. Fill in as much detail as possible (affected component, reproduction steps, impact).

This opens a private advisory where we can discuss the issue and, if needed, collaborate on a fix in a temporary private fork before any public disclosure.

### Secondary channel: Email

If you are unable to use GitHub's reporting feature, you can email **security@mail.analecta.app**.

Please include:
- A description of the vulnerability and its potential impact.
- Steps to reproduce, or a proof of concept if available.
- The affected version.

## Scope

### In-Scope

The following are the highest-priority surfaces — named explicitly because they handle untrusted input or mediate trust boundaries:

- **`analecta://` URL scheme handler** — the `id` parameter is used in database queries and must be treated as untrusted input. Injection or path traversal via this handler is in scope.
- **Tier-2 render channel** — the loopback HTTP server (`127.0.0.1:{port}`) used for web content rendering, and its `X-Render-Token` bearer authentication. Token bypass or SSRF via this channel is in scope.
- **Vault-scoped filesystem access** — path traversal or sandbox escape through the `analecta-file://` protocol handler or any IPC channel that accesses vault files.
- **Asset downloader** — bypassing `Content-Type` validation to write non-image content to the vault asset directory.

Additionally in scope:

- Electron process boundary violations (main/renderer/preload) and contextBridge IPC surface.
- Content Security Policy (CSP) configuration and any bypass thereof.
- Untrusted remote content processed during web extraction (XSS via fetched pages, Chromium renderer sandbox escape).
- SQLite storage — SQL injection or unauthorized data access through the application.
- SvelteKit/Svelte 5 frontend logic where it handles user data or communicates with the sidecar.

### Out-of-Scope
- Vulnerabilities in upstream third-party dependencies (npm packages, PyPI packages, Electron/Chromium itself). Please report these to the respective upstream maintainers. We will update dependencies promptly once a fix is available upstream.
- Operating system, kernel, display server (Wayland/COSMIC), or runtime-level vulnerabilities.
- Denial of Service (DoS) attacks against the application.
- Social engineering or phishing attempts targeting the maintainer or users.
- Issues requiring physical access to an already-compromised machine.

## Our Timeline Commitments

As this project has a single maintainer, response times are best-effort but prioritized:

- **Initial acknowledgment:** Within 3–5 business days of receiving the report.
- **Triage and severity assessment:** Within 10 business days, you will receive an update on whether the vulnerability is confirmed and its assessed severity.
- **Remediation and disclosure:** We aim to release a fix within the industry-standard **90-day coordinated disclosure window**. Actual timing within that window depends on severity and maintainer availability — critical issues affecting user data or remote code execution are prioritized over lower-severity findings.

If a fix cannot be completed within 90 days, we will communicate this directly and explain the reason (e.g., architectural complexity) rather than let the deadline pass silently.

## Recognition

If you'd like credit for a responsibly disclosed vulnerability, let us know in your report. Credit will be added to the published security advisory unless you prefer to remain anonymous.

## Supply-Chain & Dependency Security

Analecta maintains documented controls for dependency integrity, CI/CD pipeline security, and supply-chain risk management:

- [`docs/dependency-verification.md`](../docs/dependency-verification.md) — per-dependency hash verification protocol for npm/pnpm and Python/uv, including cross-checks against registry integrity fields and lockfile diffs.
- [`docs/github-actions-security.md`](../docs/github-actions-security.md) — GitHub Actions security model: SHA-pinned action references, per-job least-privilege permissions, age-gated dependency updates, and SLSA provenance attestation verification.
- [`docs/socket-security.md`](../docs/socket-security.md) — continuous dependency security scanning via Socket, including the false-positive catalog, dismissed Dependabot alerts, and resolved CVE history.
