#!/usr/bin/env bash
# Validate the built Alpine runtime and all three supervised processes.
set -euo pipefail

image=${1:?Image to validate is required}
docker run --rm --entrypoint python "$image" -m pip check
docker run --rm --entrypoint /usr/local/bin/watchtower "$image" --version
docker run --rm --entrypoint python \
  --volume "$PWD/dashboard/tests:/app/tests:ro" \
  "$image" -m unittest discover -s /app/tests -v

# The worker stays disabled; Watchtower only reads containers with opt-in labels.
container_id=$(docker run --detach --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
  --tmpfs /config:rw,noexec,nosuid,nodev,size=16m \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock,readonly \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --label com.centurylinklabs.watchtower.enable=false \
  --env CONFIG_DIR=/config \
  --env WATCHTOWER_HTTP_API_ENDPOINTS=update,metrics \
  --env WATCHTOWER_HTTP_API_PERIODIC_POLLS=true \
  --env WATCHTOWER_HTTP_API_TOKEN=test-watchtower-token-at-least-32-chars \
  --env WATCHTOWER_MONITOR_ONLY=true \
  --env WATCHTOWER_LABEL_ENABLE=true \
  --env DASHBOARD_USERNAME=test-user \
  --env DASHBOARD_PASSWORD=test-password \
  --env SECRET_KEY=test-secret-key-with-at-least-32-characters \
  "$image")

cleanup() {
  docker logs "$container_id" || true
  docker rm --force "$container_id" >/dev/null || true
}
trap cleanup EXIT

health=starting
for ((attempt=0; attempt<90; attempt++)); do
  health=$(docker inspect --format '{{.State.Health.Status}}' "$container_id")
  if [[ "$health" == healthy ]]; then
    break
  fi
  if [[ "$health" == unhealthy ]]; then
    break
  fi
  sleep 1
done
if [[ "$health" != healthy ]]; then
  echo "Container did not become healthy (status: $health)" >&2
  exit 1
fi

docker exec "$container_id" python -c '
import requests
base = "http://127.0.0.1:5000"
assert requests.get(base, timeout=10).status_code == 401
auth = ("test-user", "test-password")
for path in ("/", "/settings"):
    response = requests.get(base + path, auth=auth, timeout=10)
    assert response.status_code == 200, (path, response.status_code)
response = requests.get(
    "http://127.0.0.1:8080/v1/metrics",
    headers={"Authorization": "Bearer test-watchtower-token-at-least-32-chars"},
    timeout=10,
)
assert response.status_code == 200, response.status_code
print("Dashboard authentication, pages and Watchtower metrics: OK")
'
