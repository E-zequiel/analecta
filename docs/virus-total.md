# VirusTotal Integration

This document covers the legal, licensing, and UX constraints that govern the VirusTotal integration in Analecta.

---

## Terms of Service

1. **Non-commercial use only.** The VirusTotal Public API is free but its ToS explicitly prohibit use in commercial applications, products, or services. As long as Analecta remains free and open-source, usage is permitted. Any monetisation path (freemium, forced donations, advertising, commercial support) requires a Premium API licence.

2. **Not an antivirus substitute.** The ToS prohibit use as a replacement for antivirus products or in ways that harm the antivirus industry.

3. **Rate limits.** The Public API enforces a strict limit of **4 requests per minute and 500 requests per day**. Exceeding these limits results in a permanent account ban. The polling loop in `security/virustotal.py` enforces a minimum of 15 seconds between consecutive API calls.

4. **URL privacy.** Every URL submitted to VirusTotal is indexed in their public database and made available to the security research community. Analecta must display a one-time opt-in disclaimer before the first scan and must never submit URLs silently.

---

## Licensing

VirusTotal does not mandate a specific open-source licence. Because Analecta consumes the API over HTTP/REST — it does not link against any VirusTotal library or binary — permissive licences (MIT, Apache 2.0) and copyleft licences (GPLv3) are all compatible.

**API Key handling (critical):** The API key must never be hardcoded. Each user must register on VirusTotal, obtain their own Public API key, and enter it in the app. This makes each user independently responsible for their ToS compliance and rate-limit consumption.

The key is stored in the system keyring via the `keyring` library. It is never written to `config.toml`, environment variables, or logs. See `docs/bitwarden-secrets-manager.md` for the secret management architecture.

---

## Opt-In Disclaimer (required before first scan)

The following text must be displayed before the user can enable VirusTotal scanning. It must be presented as a modal with explicit confirmation — not inlined in a settings form.

```
VirusTotal is a free service provided by Google that analyses URLs using
over 70 antivirus scanners and URL/domain blocklisting services.

⚠  PRIVACY WARNING
Every URL you submit is permanently indexed in VirusTotal's public database
and shared with the global security community. Do NOT submit URLs that contain
session tokens, API keys, passwords, or any Personally Identifiable Information
(PII) in their query parameters.

By proceeding, you confirm that:
• You are providing your own Personal API Key.
• Your use of this integration is strictly non-commercial.
• You are solely responsible for complying with VirusTotal's Terms of Service
  and API rate limits (4 req/min · 500 req/day).

Enable VirusTotal scanning and configure your API Key?
```

Include plain-text links to VirusTotal's Terms of Service and Privacy Notice where the UI allows it.
