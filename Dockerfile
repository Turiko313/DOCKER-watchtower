# Custom Watchtower image -- pinned version for independence
# Rebuild and push to ghcr.io/turiko313/watchtower:latest
# whenever you want to pick up upstream changes.
ARG WATCHTOWER_VERSION=latest
FROM containrrr/watchtower:${WATCHTOWER_VERSION}

LABEL org.opencontainers.image.source="https://github.com/turiko313/DOCKER-watchtower"
LABEL org.opencontainers.image.description="Custom Watchtower build"
