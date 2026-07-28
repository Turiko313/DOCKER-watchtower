import logging
import os
import re
import shlex
import time

import docker
from flask import flash

logger = logging.getLogger(__name__)

docker_client = None
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

def get_docker_client():
    global docker_client
    try:
        if docker_client is None:
            docker_client = docker.from_env(version="auto")
        docker_client.ping()
    except Exception:
        try:
            docker_client = docker.from_env(version="auto")
            docker_client.ping()
        except Exception:
            docker_client = None
            raise
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

def _watchtower_container(client):
    """Find this all-in-one container even when Compose renamed it."""
    candidates = (
        os.environ.get("WATCHTOWER_CONTAINER_NAME", "").strip(),
        os.environ.get("HOSTNAME", "").strip(),
        "watchtower-dashboard",
        "watchtower",
    )
    attempted = set()
    for identifier in candidates:
        if not identifier or identifier in attempted:
            continue
        attempted.add(identifier)
        try:
            return client.containers.get(identifier)
        except Exception:
            continue
    raise RuntimeError("Conteneur Watchtower introuvable")


def _log_fields(line):
    """Parse Logrus logfmt fields while tolerating Supervisor prefixes."""
    fields = {}
    try:
        tokens = shlex.split(_ANSI_ESCAPE_RE.sub("", line), posix=True)
    except ValueError:
        return fields
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            fields[key] = value
    return fields


def _container_field(fields):
    return (fields.get("container") or fields.get("container_name") or "").lstrip("/")


def _parse_update_statuses(log_text):
    """Extract per-container outcomes from legacy and v1.20+ Watchtower logs."""
    statuses = {}
    pending_updates = set()
    failed_in_session = set()

    for raw_line in log_text.splitlines():
        line = _ANSI_ESCAPE_RE.sub("", raw_line).strip()
        if not line:
            continue

        # Legacy containrrr/watchtower messages.
        legacy_update = re.search(r'Creating /([^\s"]+)', line)
        if legacy_update:
            statuses[legacy_update.group(1).strip()] = "updated"
            continue
        if "Unable to update container" in line:
            legacy_failure = re.search(
                r'Unable to update container.*?/([^\s"\\]+)', line
            )
            if legacy_failure:
                statuses[legacy_failure.group(1).strip()] = "failed"
            continue

        fields = _log_fields(line)
        message = fields.get("msg", "")
        container_name = _container_field(fields)

        if message == "Rolling restart compatibility validation failed":
            dependency_error = fields.get("error", line)
            dependency_match = re.search(
                r'(?:"([^"]+)"|([A-Za-z0-9_.-]+))\s+depends on',
                dependency_error,
            )
            if dependency_match:
                failed_name = dependency_match.group(1) or dependency_match.group(2)
                statuses[failed_name] = "failed"
                failed_in_session.add(failed_name)
                pending_updates.discard(failed_name)
            continue

        if message == "Found new image" and container_name:
            pending_updates.add(container_name)
            failed_in_session.discard(container_name)
            continue

        message_lower = message.lower()
        terminal_failure = (
            fields.get("level", "").lower() in {"error", "fatal", "panic"}
            or message_lower.startswith(
                (
                    "failed to stop",
                    "failed to start",
                    "failed to create",
                    "failed to check",
                    "image pull failed",
                )
            )
        )
        if container_name and terminal_failure:
            statuses[container_name] = "failed"
            failed_in_session.add(container_name)
            pending_updates.discard(container_name)
            continue

        if container_name and (
            "skipped" in message.lower()
            or "monitor-only" in message.lower()
        ):
            pending_updates.discard(container_name)
            continue

        if message == "Update session completed":
            try:
                failed_count = int(fields.get("failed", "0"))
            except ValueError:
                failed_count = len(failed_in_session)
            try:
                updated_count = int(fields.get("updated", "0"))
            except ValueError:
                updated_count = 0

            # The summary provides exact counts even though successful updates
            # are only named at debug level. Attribute outcomes only when the
            # count makes the result unambiguous.
            if updated_count == len(pending_updates):
                for name in pending_updates:
                    statuses[name] = "updated"
            elif (
                updated_count == 0
                and failed_count - len(failed_in_session) == len(pending_updates)
            ):
                for name in pending_updates:
                    statuses[name] = "failed"

            pending_updates.clear()
            failed_in_session.clear()

    return statuses


def get_update_statuses():
    try:
        client = get_docker_client()
        watchtower = _watchtower_container(client)
        logs = watchtower.logs(
            since=int(time.time()) - 86400,
            stdout=True,
            stderr=True,
        )
        return _parse_update_statuses(logs.decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("Unable to derive Watchtower update statuses: %s", exc)
        return {}
