#!/usr/bin/env bash
# Validate the built Alpine runtime and all three supervised processes.
set -euo pipefail

image=${1:?Image to validate is required}
docker run --rm --entrypoint python "$image" -m pip check
docker run --rm --entrypoint /usr/local/bin/watchtower "$image" --help >/dev/null
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
  --env WATCHTOWER_NO_PULL=true \
  --env WATCHTOWER_LABEL_ENABLE=true \
  --env DASHBOARD_USERNAME=test-user \
  --env DASHBOARD_PASSWORD=test-password \
  --env SECRET_KEY=test-secret-key-with-at-least-32-characters \
  "$image")

probe_ids=()
probe_names=()
cleanup() {
  docker logs "$container_id" || true
  if ((${#probe_names[@]})); then
    docker rm --force "${probe_names[@]}" >/dev/null || true
  fi
  docker rm --force "$container_id" >/dev/null || true
}
trap cleanup EXIT

# Same image and similar names: a targeted scan/restart must leave the sibling
# untouched. These disposable probes never run Watchtower or application data.
probe_name="watchtower-smoke-${container_id:0:12}.one"
sibling_name="${probe_name}-other"
probe_image="watchtower-manual-probe:${container_id:0:12}"
docker tag "$image" "$probe_image"
for name in "$probe_name" "$sibling_name"; do
  enabled=true
  if [[ "$name" == "$probe_name" ]]; then enabled=false; fi
  probe_ids+=("$(docker run --detach --read-only --no-healthcheck \
    --security-opt no-new-privileges:true --cap-drop ALL \
    --name "$name" \
    --label com.centurylinklabs.watchtower.enable="$enabled" \
    --label com.centurylinklabs.watchtower.monitor-only=true \
    --label com.centurylinklabs.watchtower.no-pull=true \
    --entrypoint python "$probe_image" -c 'import time; time.sleep(600)')")
  probe_names+=("$name")
done
# Publish a new local image ID without fetching anything or changing probe data.
docker commit --change 'LABEL ci.manual-update=new' "${probe_ids[0]}" "$probe_image" >/dev/null

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

docker exec --env TARGET_NAME="$probe_name" --env SIBLING_NAME="$sibling_name" "$container_id" python -c '
import os
import docker
import requests
from app import CSRF_TOKEN
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

target, sibling = os.environ["TARGET_NAME"], os.environ["SIBLING_NAME"]
client = docker.from_env()
before = {name: client.containers.get(name).attrs["State"]["StartedAt"] for name in (target, sibling)}
old_ids = {name: client.containers.get(name).image.id for name in (target, sibling)}
headers = {"X-CSRF-Token": CSRF_TOKEN}
assert requests.post(base + "/restart", auth=auth, data={"container": target}, timeout=10).status_code == 403
assert requests.post(base + "/restart", auth=auth, headers=headers, timeout=10).status_code == 400
api_headers = {"Authorization": "Bearer test-watchtower-token-at-least-32-chars"}
response = requests.post("http://127.0.0.1:8080/v1/update", headers=api_headers,
                         params={"container": target}, timeout=30)
assert response.status_code == 200, response.text
assert response.json()["summary"]["scanned"] == 0, response.text
response = requests.post(base + "/update", auth=auth, headers=headers, data={"container": target}, timeout=90)
assert response.status_code == 200, (response.status_code, response.text)
assert response.json()["status"] == "success", response.text
assert response.json()["message"].startswith("Vérification terminée : 1 vérifié(s),"), response.text
assert "1 mis à jour" in response.json()["message"], response.text
assert client.containers.get(target).image.id != old_ids[target]
assert client.containers.get(sibling).image.id == old_ids[sibling]
assert client.containers.get(target).labels["com.centurylinklabs.watchtower.enable"] == "false"
response = requests.post("http://127.0.0.1:8080/v1/update", headers=api_headers,
                         params={"container": target}, timeout=30)
assert response.status_code == 200, response.text
assert response.json()["summary"]["scanned"] == 0, response.text
response = requests.post(base + "/restart", auth=auth, headers=headers, data={"container": target}, timeout=60)
assert response.status_code == 200, (response.status_code, response.text)
assert response.json()["status"] == "success", response.text
for name in (target, sibling):
    state = client.containers.get(name).attrs["State"]
    assert state["Status"] == "running", (name, state["Status"])
    assert (state["StartedAt"] != before[name]) == (name == target), name
response = requests.get(base + "/restart/status", auth=auth, params={"container": target}, timeout=10)
assert response.status_code == 200, response.text
assert [c["name"] for c in response.json()["containers"]] == [target], response.text
print("Independent manual update and Docker restart: OK; labels and automatic exclusion preserved; sibling untouched")
'
