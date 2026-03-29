# =============================================================
# Stage 1 – Build Watchtower from source
# =============================================================
FROM golang:1.22-alpine AS builder

RUN apk add --no-cache git

WORKDIR /src
RUN git clone --depth 1 https://github.com/containrrr/watchtower.git .
RUN CGO_ENABLED=0 go build -o /watchtower .

# =============================================================
# Stage 2 – Final image: Python + Watchtower + Dashboard
# =============================================================
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor procps ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

# Watchtower binary
COPY --from=builder /watchtower /usr/local/bin/watchtower
RUN chmod +x /usr/local/bin/watchtower

# Dashboard dependencies
WORKDIR /app
COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Dashboard code
COPY dashboard/ .

# Supervisor and entrypoint configs
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY start_watchtower.sh /usr/local/bin/start_watchtower.sh
RUN chmod +x /usr/local/bin/start_watchtower.sh

LABEL org.opencontainers.image.source="https://github.com/turiko313/DOCKER-watchtower"
LABEL org.opencontainers.image.description="Watchtower + Dashboard"

EXPOSE 8080 5000

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
