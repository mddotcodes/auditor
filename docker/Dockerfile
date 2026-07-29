# syntax=docker/dockerfile:1.6
# Auditor runtime image — Foundry + Slither + Python package (M1.1)
#
# Build (from repo root):
#   make build-image
#   docker build -f docker/Dockerfile -t auditor:local .
#
# Pin file: docker/versions.env (keep ARG defaults in sync).
#
# solc strategy:
#   - Foundry manages solc via forge/svm (~/.svm). First `forge build` may
#     download compilers when the version is not yet cached for the user.
#   - solc-select installs a pinned default solc at image build time for
#     standalone Slither / crytic-compile (non-Foundry) paths.
#   Prefer Foundry-based projects so Slither reuses project compilation.

# ---------------------------------------------------------------------------
# Stage 1: Foundry toolchain (prebuilt release binaries only)
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim AS foundry

ARG FOUNDRY_VERSION=v1.7.1
ARG TARGETARCH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Map Docker TARGETARCH → Foundry release arch names.
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) ARCH=amd64 ;; \
      arm64) ARCH=arm64 ;; \
      *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/foundry-rs/foundry/releases/download/${FOUNDRY_VERSION}/foundry_${FOUNDRY_VERSION}_linux_${ARCH}.tar.gz"; \
    curl -fsSL "$url" -o /tmp/foundry.tar.gz; \
    mkdir -p /opt/foundry/bin; \
    tar -xzf /tmp/foundry.tar.gz -C /opt/foundry/bin; \
    chmod +x /opt/foundry/bin/*; \
    /opt/foundry/bin/forge --version; \
    /opt/foundry/bin/cast --version

# ---------------------------------------------------------------------------
# Stage 2: Runtime — slim Python + tools + package
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ARG FOUNDRY_VERSION=v1.7.1
ARG SLITHER_VERSION=0.11.6
ARG SOLC_SELECT_VERSION=1.2.0
ARG SOLC_VERSION=0.8.28

LABEL org.opencontainers.image.title="auditor" \
      org.opencontainers.image.description="Isolated EVM audit engine (Foundry + Slither + Python)" \
      org.opencontainers.image.source="https://github.com/md/auditor" \
      auditor.foundry.version="${FOUNDRY_VERSION}" \
      auditor.slither.version="${SLITHER_VERSION}" \
      auditor.solc.default="${SOLC_VERSION}"

# Minimal runtime deps: git for forge remappings / deps; ca-certs for HTTPS.
# Foundry binaries are multi-stage copied (no rustc/curl in final layers).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

# Foundry binaries
COPY --from=foundry /opt/foundry/bin/forge /usr/local/bin/forge
COPY --from=foundry /opt/foundry/bin/cast /usr/local/bin/cast
COPY --from=foundry /opt/foundry/bin/anvil /usr/local/bin/anvil
COPY --from=foundry /opt/foundry/bin/chisel /usr/local/bin/chisel

# Non-root user with stable uid/gid (Phase 1.2 hardens runtime further)
RUN groupadd --gid 10001 auditor \
    && useradd --uid 10001 --gid 10001 --home-dir /home/auditor --create-home --shell /bin/bash auditor \
    && mkdir -p /work/jobs /artifacts /tmp \
    && chown -R auditor:auditor /work /artifacts /home/auditor \
    && chmod 1777 /tmp

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    AUDIT_TIMEOUT_SECONDS=300 \
    AUDIT_JOB_ROOT=/work/jobs \
    FOUNDRY_DIR=/home/auditor/.foundry \
    XDG_CONFIG_HOME=/home/auditor/.config \
    PATH="/usr/local/bin:/home/auditor/.foundry/bin:${PATH}"

WORKDIR /tmp/build

# Install Slither (pinned) + solc-select with a default solc for offline-ish basic runs.
# Then install the project package (includes src/auditor/security/*).
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir \
        "slither-analyzer==${SLITHER_VERSION}" \
        "solc-select==${SOLC_SELECT_VERSION}" \
    && solc-select install "${SOLC_VERSION}" \
    && solc-select use "${SOLC_VERSION}" \
    && pip install --no-cache-dir . \
    && rm -rf /tmp/build /root/.cache /home/auditor/.cache \
    && mkdir -p /tmp/build

# Entrypoint
COPY docker/entrypoint.sh /usr/local/bin/auditor-entrypoint
RUN chmod 755 /usr/local/bin/auditor-entrypoint

# solc-select stores binaries under the installing user's home; re-home for auditor.
# solc-select defaults to ~/.solc-select — copy if present from root install.
RUN if [ -d /root/.solc-select ]; then \
      cp -a /root/.solc-select /home/auditor/.solc-select; \
      chown -R auditor:auditor /home/auditor/.solc-select; \
    fi \
    && mkdir -p /home/auditor/.svm /home/auditor/.foundry \
    && chown -R auditor:auditor /home/auditor

WORKDIR /work

USER auditor

# Confirm Foundry binaries are executable as non-root. forge auto-downloads
# solc into ~/.svm on first compile when the version is missing.
RUN forge --version && cast --version

# Optional: print versions at start when AUDIT_PRINT_VERSIONS=1
ENV AUDIT_PRINT_VERSIONS=0

ENTRYPOINT ["/usr/local/bin/auditor-entrypoint"]
CMD ["versions"]

# Cheap toolchain presence check (long interval — slither --version is not free).
HEALTHCHECK --interval=5m --timeout=30s --start-period=30s --retries=2 \
  CMD forge --version >/dev/null && slither --version >/dev/null && python -c "import auditor" || exit 1
