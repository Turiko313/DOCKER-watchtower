import time
import re
import docker
import logging
from flask import flash

logger = logging.getLogger(__name__)

docker_client = docker.from_env(version="auto")

def get_docker_client():
    global docker_client
    try:
        docker_client.ping()
    except Exception:
        docker_client = docker.from_env(version="auto")
    return docker_client

def list_containers():
    containers = []
    try:
        client = get_docker_client()
        for c in client.containers.list(all=True):
            try:
                try:
                    image_name = c.image.tags[0] if c.image.tags else c.image.short_id
                except Exception:
                    image_name = c.attrs.get("Config", {}).get("Image", "unknown")
                wt_label = c.labels.get("com.centurylinklabs.watchtower.enable")
                if wt_label is None:
                    wt_enabled = True
                else:
                    wt_enabled = wt_label.lower() != "false"

                state = c.attrs.get("State", {})
                exit_code = state.get("ExitCode")
                finished_at = state.get("FinishedAt", "")
                if finished_at and not finished_at.startswith("0001"):
                    finished_at = finished_at[:19].replace("T", " ")
                else:
                    finished_at = ""

                info = {
                    "name": c.name,
                    "image": image_name,
                    "status": c.status,
                    "id": c.short_id,
                    "watchtower_enabled": wt_enabled,
                    "created": c.attrs.get("Created", "")[:19].replace("T", " "),
                    "exit_code": exit_code if c.status in ("exited", "dead") else None,
                    "finished_at": finished_at if c.status in ("exited", "dead") else "",
                }
                containers.append(info)
            except Exception:
                try:
                    name = c.name or c.short_id or "unknown"
                    _state = c.attrs.get("State", {})
                    _status = _state.get("Status", "unknown")
                    _exit_code = _state.get("ExitCode")
                    _fin = _state.get("FinishedAt", "")
                    if _fin and not _fin.startswith("0001"):
                        _fin = _fin[:19].replace("T", " ")
                    else:
                        _fin = ""
                    containers.append({
                        "name": name,
                        "image": c.attrs.get("Config", {}).get("Image", "unknown"),
                        "status": _status,
                        "id": c.short_id or "?",
                        "watchtower_enabled": False,
                        "created": c.attrs.get("Created", "")[:19].replace("T", " "),
                        "exit_code": _exit_code if _status in ("exited", "dead") else None,
                        "finished_at": _fin if _status in ("exited", "dead") else "",
                    })
                except Exception as inner_exc:
                    logger.warning("Skipping container: %s", inner_exc)
    except Exception as exc:
        flash(f"Erreur Docker : {exc}", "error")

    containers.sort(key=lambda x: x["name"])
    return containers

def get_update_statuses():
    statuses = {}
    try:
        client = get_docker_client()
        wt = client.containers.get("watchtower-dashboard")
        logs = wt.logs(since=int(time.time()) - 86400, stdout=True, stderr=True)
        for line in logs.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            m = re.search(r'Creating /([^\s"]+)', line)
            if m:
                name = m.group(1).strip()
                if name:
                    statuses[name] = "updated"
            elif "Unable to update container" in line:
                m = re.search(r'Unable to update container.*?/([^\s"\\]+)', line)
                if m:
                    name = m.group(1).strip()
                    if name:
                        statuses[name] = "failed"
    except Exception:
        pass
    return statuses
