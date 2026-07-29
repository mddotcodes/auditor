# Contributing to Auditor

Thanks for your interest in contributing. This guide covers how to report issues, set up a development environment, and open pull requests.

## Reporting bugs and proposing features

- **Bugs:** Open a GitHub issue with steps to reproduce, expected vs actual behavior, and environment details (OS, Docker version, commit SHA if known).
- **Features:** Open an issue describing the use case, proposed behavior, and any alternatives you considered.
- **Security vulnerabilities:** Do **not** file a public issue. Follow [SECURITY.md](SECURITY.md) instead.

## Development setup

Requires Python 3.12+ and [Make](https://www.gnu.org/software/make/).

```bash
git clone https://github.com/<org>/auditor.git
cd auditor
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
make install                # editable install + dev tools (ruff, mypy, pytest)
make lint
make test
make build-image            # no-op until a Dockerfile exists (Phase 1)
```

See [docs/decisions/0001-orchestration-language.md](docs/decisions/0001-orchestration-language.md) for toolchain choices.

## Pull request expectations

- Keep PRs focused: one logical change per PR.
- Ensure CI is green (lint and tests) before requesting review.
- Never commit secrets, API keys, or private credentials.
- Update docs when behavior or public interfaces change.
- Be respectful and follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Code of Conduct

Participation in this project is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By contributing, you agree to uphold it.

## Branch protection

Maintainers should configure GitHub branch protection on `main` as follows:

- **Require a pull request before merging** to `main` (no direct pushes).
- **Require status checks to pass** before merging. The CI workflow (`.github/workflows/ci.yml`) defines these jobs — use the exact names when selecting required checks:
  - `lint-and-test`
  - `docker`
- **Recommend at least 1 approving review** for external (non-maintainer) PRs.
- **Dependabot PRs** still need green CI before merge; do not auto-merge without passing checks.
