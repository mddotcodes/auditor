# Security Policy

## Supported versions

Only the latest `main` branch and the latest tagged release (once releases are published) receive security fixes. Older commits and untagged snapshots are unsupported.

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report vulnerabilities privately using **GitHub Security Advisories** on this repository (preferred). If advisory reporting is unavailable, contact maintainers through a private channel they designate.

Please include:

- A clear description of the issue and its impact
- Steps to reproduce, or a minimal proof of concept when safe to share
- Affected version, commit, or image tag if known

We will acknowledge reports as soon as practical and work with you on disclosure timing.

## Security-relevant notes for this project

Auditor executes **untrusted Solidity** (and related Foundry project inputs) inside a pipeline that compiles, analyzes, and may generate and run tests. Issues involving sandbox escape, insufficient isolation, resource exhaustion, timeout bypass, path traversal into host mounts, or injection into tool invocations are security-relevant and should be reported privately as above.

Do not include secrets (API keys, tokens, private keys) in issues, PRs, or sample inputs committed to the repository.
