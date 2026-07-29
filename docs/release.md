# Releasing Auditor

How to cut a version tag and publish the runtime image to GitHub Container Registry (GHCR).

## Prerequisites

Repository **owner/admin** must allow the release workflow to write packages:

1. **Settings → Actions → General → Workflow permissions**  
   Choose **Read and write permissions** (or otherwise grant `packages: write` to `GITHUB_TOKEN` for the Release workflow).
2. On first successful push, GitHub creates a package under the repo/org.  
   Set package **visibility** (public/private) under **Packages** as appropriate.

Without package write access, the workflow still **builds** the image and soft-fails the push (see workflow summary).

## Versioning

- Follow [Semantic Versioning](https://semver.org/).
- Git tags are `v` + semver (e.g. `v0.1.0`).
- Keep [`CHANGELOG.md`](../CHANGELOG.md) updated under **Unreleased**, then move notes into a new version section when tagging.
- Align `version` in [`pyproject.toml`](../pyproject.toml) with the tag (without the `v` prefix) when you intend a real release.

## Tag and publish

From a clean `main` (or the commit you want to ship):

```bash
# 1. Update CHANGELOG.md (move Unreleased → ## [x.y.z] — YYYY-MM-DD)
# 2. Bump version in pyproject.toml if needed
# 3. Commit those changes on the release commit

git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

Pushing a tag matching `v*` runs [`.github/workflows/release.yml`](../.github/workflows/release.yml):

1. Checkout  
2. `docker build -f docker/Dockerfile` as  
   `ghcr.io/<owner>/<repo>:<tag>` and `:latest`  
   (`github.repository`, lowercased for GHCR)  
3. Log in to `ghcr.io` with `GITHUB_TOKEN`  
4. Push both tags (soft-fail if permissions are missing)

## Pull

```bash
docker pull ghcr.io/<owner>/<repo>:v0.1.0
# or
docker pull ghcr.io/<owner>/<repo>:latest
```

Replace `<owner>/<repo>` with this repository’s GitHub path (e.g. `myorg/auditor`).

## Local dry-run (no registry)

Build a release-shaped image on your machine without tagging git or pushing:

```bash
make release-dry-run
# → auditor:local (and auditor:release-dry-run); no push
```

Equivalent to a local `docker build -f docker/Dockerfile` only.

## What this workflow does **not** do

- Create GitHub Releases / attach binaries (optional follow-up)
- Generate or attach an SBOM (optional)
- Publish to Docker Hub or PyPI

## Security support

Only the **latest tagged release** and current `main` receive security fixes. See [SECURITY.md](../SECURITY.md).
