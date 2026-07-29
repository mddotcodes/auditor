# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Release packaging scaffolding: GHCR publish workflow on `v*` tags, release how-to, changelog.

## [0.0.1] — scaffold

Pre-release scaffold only (not a published product release). Package version in `pyproject.toml` is `0.0.1`.

### Added

- Docker runtime image (`docker/Dockerfile`) with Foundry, Slither, and the auditor package
- Pipeline stages, job artifacts, HTTP API + WebSocket events, CLI
- Security policy, threat model notes, and hardened runtime defaults
- CI (lint/test + image build) and local Compose / `make` targets

[Unreleased]: https://github.com/mddotcodes/auditor/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/mddotcodes/auditor/releases/tag/v0.0.1
