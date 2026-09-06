#!/usr/bin/env python3
"""Run restricted OCC commands once when the Nextcloud image changes."""

import json
import logging
import os
from pathlib import PurePosixPath
import tempfile
import time

import docker
from docker.errors import DockerException, NotFound

from nextcloud_hooks import NEXTCLOUD_CONTAINER_NAME, parse_nextcloud_commands
from settings import CONFIG_DIR, load_settings


STATE_FILE = os.path.join(CONFIG_DIR, "nextcloud_post_update_state.json")
POLL_SECONDS = 10
STARTUP_GRACE_SECONDS = 15
READY_TIMEOUT_SECONDS = 600
DOCKER_TIMEOUT_SECONDS = 600
MAX_STATUS_OUTPUT_BYTES = 65536

logger = logging.getLogger("nextcloud-post-update")


def _load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as state_file:
            state = json.load(state_file)
        return state if isinstance(state, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    fd, temporary_file = tempfile.mkstemp(
        dir=os.path.dirname(STATE_FILE), prefix=".nextcloud-hook-", suffix=".json"
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, indent=2)
            state_file.write("\n")
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_file, STATE_FILE)
    except OSError:
        try:
            os.unlink(temporary_file)
        except FileNotFoundError:
            pass
        raise


def _running_container(client):
    try:
        container = client.containers.get(NEXTCLOUD_CONTAINER_NAME)
    except NotFound:
        return None
    container.reload()
    if container.status != "running":
        return None
    image_id = container.attrs.get("Image", "")
    return (container, image_id) if image_id else None


def _exec_occ(container, argv, user, capture_limit=0):
    api = container.client.api
    created = api.exec_create(
        container.id, list(argv), stdout=True, stderr=True, user=user,
        tty=False, stdin=False, privileged=False,
    )
    exec_id = created["Id"]
    output = bytearray()
    stream = api.exec_start(
        exec_id, detach=False, tty=False, stream=True, socket=False, demux=False,
    )
    try:
        for chunk in stream:
            # Retain only the bounded status response; discard command output.
            remaining = capture_limit - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()
    return api.exec_inspect(exec_id).get("ExitCode"), bytes(output)


def _occ_ready(container, command):
    prefix_length = 2 if str(PurePosixPath(command.argv[0])) == "php" else 1
    argv = (*command.argv[:prefix_length], "status", "--output=json")
    try:
        exit_code, output = _exec_occ(
            container, argv, command.user, capture_limit=MAX_STATUS_OUTPUT_BYTES
        )
        status = json.loads(output)
    except (DockerException, ValueError, UnicodeError):
        return False
    return (
        exit_code == 0
        and isinstance(status, dict)
        and status.get("installed") is True
        and status.get("maintenance") is False
        and status.get("needsDbUpgrade") is False
    )


def _wait_until_ready(client, image_id, command, sleep_fn=time.sleep):
    sleep_fn(STARTUP_GRACE_SECONDS)
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while True:
        current = _running_container(client)
        if current is not None and current[1] != image_id:
            return None
        if current is not None:
            container = current[0]
            health = container.attrs.get("State", {}).get("Health", {}).get("Status")
            if (not health or health == "healthy") and _occ_ready(container, command):
                return container
        if time.monotonic() >= deadline:
            return None
        sleep_fn(5)


def _run_commands(container, commands):
    for index, command in enumerate(commands, start=1):
        logger.info("Execution de la commande OCC %s/%s.", index, len(commands))
        exit_code, _ = _exec_occ(container, command.argv, command.user)
        if exit_code != 0:
            logger.error(
                "La commande OCC %s/%s a echoue avec le code %s; la suite est annulee.",
                index,
                len(commands),
                exit_code,
            )
            return False
    return True


def process_once(client, sleep_fn=time.sleep):
    """Inspect Nextcloud and attempt the hook once per observed image transition."""
    current = _running_container(client)
    if current is None:
        return "unavailable"
    _, image_id = current

    state = _load_state()
    previous_image_id = state.get("image_id", "")
    attempted_image_id = state.get("hook_attempted_image_id", "")

    if not previous_image_id:
        _save_state({"image_id": image_id, "hook_attempted_image_id": image_id})
        logger.info("Image Nextcloud initiale enregistree; aucun hook execute.")
        return "initialized"

    if previous_image_id != image_id:
        state["image_id"] = image_id
        _save_state(state)
        logger.info("Nouvelle image Nextcloud detectee.")
    elif attempted_image_id == image_id:
        return "unchanged"

    settings = load_settings()
    state["hook_attempted_image_id"] = image_id
    _save_state(state)

    if not settings.get("nextcloud_post_update_enabled"):
        logger.info("Hook Nextcloud desactive; aucune commande executee.")
        return "disabled"

    try:
        commands = parse_nextcloud_commands(
            settings.get("nextcloud_post_update_commands", "")
        )
    except ValueError as exc:
        logger.error("Configuration du hook Nextcloud invalide: %s", exc)
        return "invalid"
    if not commands:
        logger.error("Hook Nextcloud active sans commande.")
        return "invalid"

    container = _wait_until_ready(client, image_id, commands[0], sleep_fn=sleep_fn)
    if container is None:
        logger.error("Nextcloud n'est pas devenu pret; commandes annulees.")
        return "not-ready"

    if _run_commands(container, commands):
        logger.info("Toutes les commandes OCC post-mise-a-jour ont reussi.")
        return "success"
    return "failed"


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [nextcloud-post-update] %(message)s",
    )
    client = None
    while True:
        try:
            if client is None:
                client = docker.from_env(version="auto", timeout=DOCKER_TIMEOUT_SECONDS)
                client.ping()
            process_once(client)
        except DockerException as exc:
            logger.warning("Docker indisponible: %s", exc)
            client = None
        except Exception:
            logger.exception("Erreur inattendue du worker Nextcloud")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
