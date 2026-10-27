# =============================================================
# Stage 1 - Build the maintained Watchtower fork from an immutable commit
# =============================================================
FROM golang:1.27.3-alpine@sha256:b8c9b82affbbb82a020bbcf998dad2ba9516944ac7e883524642a481a9505e9c AS builder

ARG WATCHTOWER_VERSION=v1.23.2
ARG WATCHTOWER_COMMIT=cd5946be205ea0a60dd31235a11f04ac9851ebf6

RUN apk add --no-cache ca-certificates git

WORKDIR /src
RUN git clone --branch "${WATCHTOWER_VERSION}" --depth 1 \
        https://github.com/nicholas-fedor/watchtower.git . \
    && test "$(git rev-parse HEAD)" = "${WATCHTOWER_COMMIT}" \
    && test "$(git describe --tags --exact-match)" = "${WATCHTOWER_VERSION}" \
    && go mod verify

# Request-local manual update policy; automatic Watchtower behavior is unchanged.
COPY watchtower/overlay/ ./
COPY watchtower/manual-update.patch /tmp/manual-update.patch
RUN git apply --check /tmp/manual-update.patch \
    && git apply /tmp/manual-update.patch \
    && gofmt -w internal/api/routes/manual_update.go internal/api/routes/manual_update_test.go \
        pkg/types/manual_update.go pkg/types/update_params.go \
        pkg/container/manual_update_test.go pkg/container/metadata.go pkg/container/client.go \
    && CGO_ENABLED=0 go test ./internal/api/routes ./pkg/container ./pkg/types \
    && CGO_ENABLED=0 go build \
        -trimpath \
        -buildvcs=false \
        -ldflags="-s -w -X github.com/nicholas-fedor/watchtower/internal/meta.Version=${WATCHTOWER_VERSION}" \
        -o /watchtower .

# =============================================================
# Stage 2 - Final image: Python + Watchtower + Dashboard
# =============================================================
FROM python:3.15.1-alpine@sha256:54448f67d7c31c39e5cdd3ea2d6382395ce142806c284425f8e897d78a0465b3

RUN apk add --no-cache ca-certificates tzdata

# Watchtower binary
COPY --from=builder /watchtower /usr/local/bin/watchtower
RUN chmod +x /usr/local/bin/watchtower

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Dashboard dependencies
WORKDIR /app
COPY dashboard/requirements.txt ./requirements.txt
RUN pip install --require-hashes -r requirements.txt

# Dashboard code - copy each file explicitly
COPY dashboard/app.py ./app.py
COPY dashboard/docker_helpers.py ./docker_helpers.py
COPY dashboard/nextcloud_hooks.py ./nextcloud_hooks.py
COPY dashboard/nextcloud_post_update.py ./nextcloud_post_update.py
COPY dashboard/settings.py ./settings.py
COPY dashboard/watchtower_api.py ./watchtower_api.py
COPY dashboard/templates/ ./templates/

# Supervisor and entrypoint configs
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY start_watchtower.py /usr/local/bin/start_watchtower.py
RUN chmod +x /usr/local/bin/start_watchtower.py

LABEL org.opencontainers.image.source="https://github.com/Turiko313/DOCKER-watchtower" \
      org.opencontainers.image.description="Maintained Watchtower fork + secured dashboard" \
      org.opencontainers.image.version="1.5.1"

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status watchtower | grep -q RUNNING \
        && supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status dashboard | grep -q RUNNING \
        && supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status nextcloud-post-update | grep -q RUNNING \
        || exit 1

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
