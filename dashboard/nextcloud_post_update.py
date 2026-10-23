#!/usr/bin/env python3
"""Run restricted OCC commands once when the Nextcloud image changes."""

import json
import logging
import os
from pathlib import PurePosixPath
import re
import tempfile
import time

import docker
import requests
from docker.errors import DockerException, NotFound

from nextcloud_hooks import NEXTCLOUD_CONTAINER_NAME, parse_nextcloud_commands
from settings import CONFIG_DIR, load_settings


STATE_FILE = os.path.join(CONFIG_DIR, "nextcloud_post_update_state.json")
POLL_SECONDS = 10
POST_UPDATE_DELAY_SECONDS = 120
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


def _resolve_command(container, command):
    """Use PHP directly; LinuxServer's occ wrapper needs sudo/root and a shell."""
    image = container.attrs.get("Config", {}).get("Image", "")
    repository = image.split("@", 1)[0].split(":", 1)[0]
    linuxserver = repository in {
        "linuxserver/nextcloud", "docker.io/linuxserver/nextcloud",
        "lscr.io/linuxserver/nextcloud", "ghcr.io/linuxserver/nextcloud",
    }
    prefix_length = 2 if str(PurePosixPath(command.argv[0])) == "php" else 1
    path = command.argv[prefix_length - 1]
    if str(PurePosixPath(path)) == "occ":
        path = "/app/www/public/occ" if linuxserver else "/var/www/html/occ"
    user = "abc" if linuxserver and not command.explicit_user else command.user
    return ("php", path, *command.argv[prefix_length:]), user


def _parse_occ_status(output):
    try:
        return json.loads(output)
    except ValueError:
        # PHP or image startup warnings may surround the single-line JSON.
        # Keep validating all readiness fields; never include raw output in logs.
        for line in output.splitlines():
            if line.lstrip().startswith(b"{"):
                try:
                    status = json.loads(line)
                except ValueError:
                    continue
                if isinstance(status, dict) and all(
                    key in status for key in ("installed", "maintenance", "needsDbUpgrade")
                ):
                    return status
        raise ValueError("No OCC status JSON") from None


def _occ_ready(container, command, diagnostics=None):
    resolved, user = _resolve_command(container, command)
    argv = (*resolved[:2], "status", "--output=json")
    reason = None
    try:
        exit_code, output = _exec_occ(
            container, argv, user, capture_limit=MAX_STATUS_OUTPUT_BYTES
        )
        if exit_code != 0:
            reason = f"occ status a échoué (code {exit_code}; vérifier l'utilisateur et le chemin OCC)"
        else:
            status = _parse_occ_status(output)
            if not isinstance(status, dict) or any(
                not isinstance(status.get(key), bool)
                for key in ("installed", "maintenance", "needsDbUpgrade")
            ):
                reason = "état OCC incomplet ou invalide"
            elif not status["installed"]:
                reason = "Nextcloud n'est pas installé"
            elif status["maintenance"]:
                reason = "Nextcloud est en maintenance"
            elif status["needsDbUpgrade"]:
                reason = "migration de base de données Nextcloud en attente (occ upgrade)"
    except DockerException:
        reason = "contrôle OCC refusé par Docker (vérifier l'utilisateur et l'exécutable)"
    except requests.RequestException:
        reason = "erreur réseau pendant le contrôle OCC"
    except (ValueError, UnicodeError):
        reason = "occ status n'a pas renvoyé un JSON valide"
    if diagnostics is not None:
        diagnostics[:] = [reason] if reason else []
    return reason is None


def _wait_until_ready(client, image_id, command, sleep_fn=time.sleep, diagnostics=None):
    if diagnostics is None:
        diagnostics = []
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while True:
        try:
            current = _running_container(client)
        except (DockerException, requests.RequestException):
            diagnostics[:] = ["Docker indisponible pendant le contrôle du conteneur"]
            current = None
        else:
            if current is None:
                diagnostics[:] = ["conteneur nextcloud absent ou arrêté"]
        if current is not None and current[1] != image_id:
            diagnostics[:] = ["image Nextcloud remplacée pendant l'attente"]
            return None
        if current is not None:
            container = current[0]
            health = container.attrs.get("State", {}).get("Health", {}).get("Status")
            if health and health != "healthy":
                health_label = health if health in {"starting", "unhealthy"} else "inconnu"
                diagnostics[:] = [f"healthcheck Docker : {health_label}"]
            elif _occ_ready(container, command, diagnostics=diagnostics):
                return container
        if time.monotonic() >= deadline:
            logger.warning("Delai d'attente Nextcloud depasse : %s.", "; ".join(diagnostics))
            return None
        sleep_fn(5)


def _command_label(command):
    # Show the OCC action, never arguments that may contain credentials.
    index = 2 if str(PurePosixPath(command.argv[0])) == "php" else 1
    return re.sub(r"[^A-Za-z0-9:_-]", "?", command.argv[index])[:40]


def _notify_discord(settings, result, lines):
    if not settings.get("notifications_discord"):
        logger.info("Notification Discord du complement Nextcloud desactivee.")
        return
    url = settings.get("discord_webhook_url", "").strip()
    if not url:
        logger.warning("Notification Discord du complement Nextcloud ignoree: webhook absent.")
        return
    # Recheck persisted configuration and never follow a webhook redirect.
    if not re.fullmatch(
        r"https://(?:discord\.com|discordapp\.com)/api/webhooks/[0-9]+/[A-Za-z0-9._-]+/?",
        url,
    ):
        logger.warning("Notification Discord ignoree: URL de webhook invalide.")
        return
    status = "OK" if result == "success" else "NOK"
    content = "\n".join([f"Complément Nextcloud {status}", *lines])
    try:
        with requests.post(
            url,
            params={"wait": "true"},
            json={"content": content, "allowed_mentions": {"parse": []}},
            timeout=15,
            allow_redirects=False,
        ) as response:
            if 200 <= response.status_code < 300:
                logger.info("Notification Discord du complement Nextcloud envoyee.")
            else:
                logger.warning("Notification Discord non envoyee (HTTP %s).", response.status_code)
    except requests.RequestException:
        # Request exceptions can include the secret webhook URL.
        logger.warning("Notification Discord non envoyee: erreur reseau.")


def _run_commands(container, commands, results=None):
    if results is None:
        results = []
    for index, command in enumerate(commands, start=1):
        logger.info("Execution de la commande OCC %s/%s.", index, len(commands))
        label = f"{index}. {_command_label(command)}"
        results.append(f"{label} : NOK (exécution interrompue)")
        argv, user = _resolve_command(container, command)
        exit_code, _ = _exec_occ(container, argv, user)
        if exit_code != 0:
            results[-1] = f"{label} : NOK (code {exit_code})"
            logger.error(
                "La commande OCC %s/%s a echoue avec le code %s; la suite est annulee.",
                index,
                len(commands),
                exit_code,
            )
            return False
        results[-1] = f"{label} : OK"
    return True


def _execute_hook(client, image_id, settings, sleep_fn, before_commands=None):
    try:
        commands = parse_nextcloud_commands(
            settings.get("nextcloud_post_update_commands", "")
        )
    except ValueError:
        logger.error("Configuration du hook Nextcloud invalide.")
        return "invalid", ["Configuration des commandes OCC invalide; aucune commande exécutée."]
    if not commands:
        logger.error("Hook Nextcloud active sans commande.")
        return "invalid", ["Aucune commande OCC configurée."]

    lines = []
    try:
        diagnostics = []
        container = _wait_until_ready(
            client, image_id, commands[0], sleep_fn=sleep_fn, diagnostics=diagnostics
        )
        if container is None:
            reason = diagnostics[0] if diagnostics else "Nextcloud indisponible"
            logger.warning("Commandes Nextcloud en attente : %s.", reason)
            return "not-ready", [
                f"{reason}; aucune commande exécutée. Nouvelle vérification automatique."
            ]
        if before_commands is not None:
            before_commands()
        success = _run_commands(container, commands, results=lines)
    except (DockerException, requests.RequestException):
        logger.warning("Execution du hook Nextcloud interrompue: erreur Docker ou reseau.")
        success = False
        if not lines:
            return "failed", ["Erreur Docker ou réseau; aucune commande exécutée."]
    for index in range(len(lines), len(commands)):
        lines.append(f"{index + 1}. {_command_label(commands[index])} : non exécutée")
    if success:
        logger.info("Toutes les commandes OCC post-mise-a-jour ont reussi.")
    return ("success" if success else "failed"), lines


def process_once(client, sleep_fn=time.sleep, now_fn=time.time):
    """Inspect Nextcloud and attempt the hook once per observed image transition."""
    current = _running_container(client)
    if current is None:
        return "unavailable"
    _, image_id = current

    state = _load_state()
    previous_image_id = state.get("image_id", "")
    if not previous_image_id:
        _save_state({"image_id": image_id, "hook_attempted_image_id": image_id})
        logger.info("Image Nextcloud initiale enregistree; aucun hook execute.")
        return "initialized"

    if previous_image_id != image_id:
        state["image_id"] = image_id
        state["pending_image_id"] = image_id
        state["pending_since"] = now_fn()
        state.pop("pending_disabled_logged", None)
        state.pop("readiness_notified", None)
        _save_state(state)
        logger.info("Nouvelle image Nextcloud detectee; attente de %s secondes.", POST_UPDATE_DELAY_SECONDS)
        return "pending"

    if (state.get("hook_attempted_image_id") == image_id
            and state.get("pending_image_id") != image_id):
        return "unchanged"

    # Recover an image transition recorded by a previous worker version or
    # interrupted before its delay was saved.
    if state.get("pending_image_id") != image_id or not isinstance(
        state.get("pending_since"), (int, float)
    ):
        state["pending_image_id"] = image_id
        state["pending_since"] = now_fn()
        state.pop("pending_disabled_logged", None)
        _save_state(state)
        logger.info("Attente du complement Nextcloud initialisee pour la nouvelle image.")
        return "pending"

    if now_fn() - state["pending_since"] < POST_UPDATE_DELAY_SECONDS:
        return "pending"

    settings = load_settings()
    if not settings.get("nextcloud_post_update_enabled"):
        if not state.get("pending_disabled_logged"):
            logger.info("Hook Nextcloud desactive; nouvelle image en attente.")
            state["pending_disabled_logged"] = True
            _save_state(state)
        return "disabled"

    def mark_attempted():
        # Persist only after readiness, immediately before the first command.
        # A crash during readiness must not consume an unexecuted update.
        state["hook_attempted_image_id"] = image_id
        for key in ("pending_image_id", "pending_since", "pending_disabled_logged", "readiness_notified"):
            state.pop(key, None)
        _save_state(state)

    result, lines = _execute_hook(
        client, image_id, settings, sleep_fn, before_commands=mark_attempted
    )
    if result == "not-ready":
        if not state.get("readiness_notified"):
            state["readiness_notified"] = True
            _save_state(state)
            _notify_discord(settings, result, lines)
    else:
        if state.get("pending_image_id") == image_id:
            mark_attempted()
        _notify_discord(settings, result, lines)
    return result


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
