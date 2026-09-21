# =============================================================
# Stage 1 - Build the maintained Watchtower fork from an immutable commit
# =============================================================
FROM golang:1.27.1-alpine@sha256:4cb7ac979db5fcc41cae44b2227ba5ab8a51e8807f40d9ba4dee20a0ad960b5b AS builder

ARG WATCHTOWER_VERSION=v1.22.2
ARG WATCHTOWER_COMMIT=32ad1ae001ab41bef94bbed3827e6caca4df01c7

RUN apk add --no-cache ca-certificates git

WORKDIR /src
RUN git clone --branch "${WATCHTOWER_VERSION}" --depth 1 \
        https://github.com/nicholas-fedor/watchtower.git . \
    && test "$(git rev-parse HEAD)" = "${WATCHTOWER_COMMIT}" \
    && test "$(git describe --tags --exact-match)" = "${WATCHTOWER_VERSION}" \
    && go mod verify \
    && CGO_ENABLED=0 go build \
        -trimpath \
        -buildvcs=false \
        -ldflags="-s -w -X github.com/nicholas-fedor/watchtower/internal/meta.Version=${WATCHTOWER_VERSION}" \
        -o /watchtower .

# =============================================================
# Stage 2 - Final image: Python + Watchtower + Dashboard
# =============================================================
FROM python:3.12-alpine@sha256:c4634f578a412db396771b61b064c6e546c9d6414c7fb5b1b05d5871f1885f7b

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
      org.opencontainers.image.version="1.2.0"

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status watchtower | grep -q RUNNING \
        && supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status dashboard | grep -q RUNNING \
        && supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status nextcloud-post-update | grep -q RUNNING \
        || exit 1

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
