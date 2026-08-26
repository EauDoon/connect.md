# Security policy

## Project status

connect.md is pre-launch software. The repository contains source code, local verification assets, and deployment contracts, but it does not advertise a supported public production service.

| Surface | Status |
| --- | --- |
| `main` source branch | Pre-launch and under active development |
| Public production deployment | Not supported or claimed |
| Security response time | No service-level commitment yet |

## Report a vulnerability

Use GitHub's **Security** tab and select **Report a vulnerability** when private vulnerability reporting is available.

If that option is unavailable, open a minimal public issue titled `Security contact requested`. Do not include exploit details, secrets, personal data, private infrastructure information, or reproduction material in the issue. Wait for a private channel before sharing sensitive details.

Include the following in a private report when possible:

- affected commit, route, component, or contract;
- impact and required attacker capabilities;
- minimal reproduction using synthetic data;
- whether the issue affects confidentiality, integrity, availability, consent, or authority;
- the smallest mitigation you believe would close the boundary.

## High-priority areas

- authorization and object ownership;
- canonical Markdown integrity and version binding;
- agent keys, grants, mandates, and consent checks;
- recruiting, representative, moderation, and lifecycle authority;
- private conversations, applications, verification evidence, and retention;
- ingestion, rendering, path handling, SSRF, injection, and resource exhaustion;
- release receipts, backups, restore, rollback, and witnessed deletion journals;
- browser-delivered secrets, CORS, Origin, Host, proxy, and security-header controls.

## Safe research boundaries

- Test only against code and environments you own or are explicitly authorized to use.
- Do not target users, third-party providers, public infrastructure, or any unrelated deployment.
- Do not use real personal data or credentials.
- Do not perform denial of service, destructive tests, social engineering, persistence, or data extraction.
- Prefer local, hermetic, synthetic reproduction with the smallest request volume.
- Stop if a test exposes information beyond your authorization.

## Disclosure boundary

Do not publish a vulnerability before a fix and disclosure plan are agreed. Acknowledgment, remediation timing, release timing, and public credit are handled case by case while the project remains pre-launch.

This policy is process guidance, not a warranty that the software is secure or production-ready.
