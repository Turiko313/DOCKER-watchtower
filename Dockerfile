# =============================================================
# Stage 1 - Build Watchtower from source (pinned to v1.7.1)
# =============================================================
FROM golang:1.22-alpine AS builder

RUN apk add --no-cache git

WORKDIR /src
RUN git clone --branch v1.7.1 --depth 1 https://github.com/containrrr/watchtower.git .
RUN VERSION=$(git describe --tags --abbrev=0 2>/dev/null || echo "dev") && \
    CGO_ENABLED=0 go build -ldflags "-X github.com/containrrr/watchtower/internal/meta.Version=$VERSION" -o /watchtower .

# =============================================================
# Stage 2 - Final image: Python + Watchtower + Dashboard
# =============================================================
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor procps ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

# Watchtower binary
COPY --from=builder /watchtower /usr/local/bin/watchtower
RUN chmod +x /usr/local/bin/watchtower

# Default Docker API version for watchtower compatibility
ENV DOCKER_API_VERSION=1.40

# Dashboard dependencies
WORKDIR /app
COPY dashboard/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Dashboard code - copy each file explicitly
COPY dashboard/app.py ./app.py
COPY dashboard/docker_helpers.py ./docker_helpers.py
COPY dashboard/settings.py ./settings.py
COPY dashboard/watchtower_api.py ./watchtower_api.py
COPY dashboard/templates/ ./templates/

# Verify files are present
RUN ls -la /app/ && ls -la /app/templates/

# Supervisor and entrypoint configs
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY start_watchtower.py /usr/local/bin/start_watchtower.py
RUN chmod +x /usr/local/bin/start_watchtower.py

LABEL org.opencontainers.image.source="https://github.com/turiko313/DOCKER-watchtower"
LABEL org.opencontainers.image.description="Watchtower + Dashboard"

EXPOSE 5000

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
