import os
import json
import re
import tempfile
from urllib.parse import urlparse

from nextcloud_hooks import parse_nextcloud_commands

CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "watchtower.json")

DEFAULTS = {
    "poll_interval": "86400",
    "schedule": "",
    "cleanup": True,
    "include_stopped": False,
    "revive_stopped": False,
    "monitor_only": False,
    "label_enable": False,
    "log_level": "info",
    "no_startup_message": True,
    "timeout": "30",
    "notifications_discord": False,
    "discord_webhook_url": "",
    "nextcloud_post_update_enabled": False,
    "nextcloud_post_update_commands": "",
}


def valid_schedule(schedule):
    """Validate the six-field cron syntax and descriptors offered by the UI."""
    if len(schedule) > 1024:
        return False
    if not schedule:
        return True
    if schedule in {"@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly"}:
        return True
    if schedule.startswith("@every "):
        duration = schedule[7:]
        number = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
        parts = re.findall(rf"({number})(ns|us|µs|μs|ms|s|m|h)", duration)
        if not parts or "".join(value + unit for value, unit in parts) != duration:
            return False
        units = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "μs": 1e-6,
                 "ms": 1e-3, "s": 1, "m": 60, "h": 3600}
        seconds = sum(float(value) * units[unit] for value, unit in parts)
        return 0 < seconds < 9223372036

    fields = schedule.split()
    if len(fields) != 6:
        return False
    months = {name: index for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
    days = {name: index for index, name in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}
    bounds = ((0, 59, {}), (0, 59, {}), (0, 23, {}), (1, 31, {}), (1, 12, months), (0, 6, days))
    for field, (minimum, maximum, names) in zip(fields, bounds):
        for expression in field.split(","):
            match = re.fullmatch(r"([^/]+)(?:/([+]?[0-9]+))?", expression)
            if not match or (match[2] is not None and not 0 < int(match[2]) <= 2**63 - 1):
                return False
            base = match[1]
            if base in {"*", "?"}:
                continue
            values = base.lower().split("-")
            if len(values) > 2:
                return False
            parsed = []
            for value in values:
                if value in names:
                    parsed.append(names[value])
                elif re.fullmatch(r"[+]?[0-9]+", value):
                    parsed.append(int(value))
                else:
                    return False
            if not all(minimum <= value <= maximum for value in parsed) or parsed[0] > parsed[-1]:
                return False
    return True

def load_settings():
    settings = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r") as fh:
            saved_settings = json.load(fh)
        if isinstance(saved_settings, dict):
            # Removed because rolling restarts can abort a complete update cycle
            # when one monitored dependency is incompatible.
            saved_settings.pop("rolling_restart", None)
            settings.update(saved_settings)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return settings


def _write_settings(settings):
    """Atomically replace the configuration to avoid a partial JSON file."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    fd, temporary_file = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".watchtower-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary_file, SETTINGS_FILE)
    except OSError:
        try:
            os.unlink(temporary_file)
        except FileNotFoundError:
            pass
        raise

def save_settings(form):
    errors = []
    current_settings = load_settings()

    try:
        poll_interval = str(
            min(604800, max(60, int(form.get("poll_interval") or 86400)))
        )
    except (ValueError, TypeError):
        poll_interval = "86400"

    try:
        timeout = str(min(3600, max(10, int(form.get("timeout") or 30))))
    except (ValueError, TypeError):
        timeout = "30"

    log_level = form.get("log_level", "info")
    if log_level not in ("debug", "info", "warn", "error", "fatal", "panic"):
        log_level = "info"

    # Validation du format Cron (Watchtower exige 6 champs ou des macros type @daily)
    schedule = " ".join(form.get("schedule", "").split())
    if not valid_schedule(schedule):
        errors.append("Le format cron est invalide : utilisez 6 champs avec des valeurs valides ou une macro reconnue. La planification cron a ete desactivee.")
        schedule = ""

    submitted_webhook = form.get("discord_webhook_url", "").strip()
    if submitted_webhook:
        parsed_webhook = urlparse(submitted_webhook)
        valid_hosts = {"discord.com", "discordapp.com"}
        webhook_parts = parsed_webhook.path.strip("/").split("/")
        valid_webhook = (
            parsed_webhook.scheme == "https"
            and parsed_webhook.hostname in valid_hosts
            and len(webhook_parts) == 4
            and webhook_parts[:2] == ["api", "webhooks"]
            and webhook_parts[2].isdigit()
            and bool(re.fullmatch(r"[A-Za-z0-9._-]+", webhook_parts[3]))
        )
        if not valid_webhook:
            errors.append("L'URL du webhook Discord est invalide et n'a pas ete modifiee.")
            submitted_webhook = ""

    nextcloud_commands = form.get("nextcloud_post_update_commands", "").strip()
    nextcloud_enabled = "nextcloud_post_update_enabled" in form
    try:
        parsed_nextcloud_commands = parse_nextcloud_commands(nextcloud_commands)
    except ValueError as exc:
        errors.append(f"Commandes post-mise-a-jour Nextcloud invalides: {exc}")
        nextcloud_commands = current_settings.get(
            "nextcloud_post_update_commands", ""
        )
        nextcloud_enabled = False
        parsed_nextcloud_commands = []
    if nextcloud_enabled and not parsed_nextcloud_commands:
        errors.append(
            "Le hook Nextcloud a ete desactive car aucune commande OCC valide n'est configuree."
        )
        nextcloud_enabled = False

    settings = {
        "poll_interval": poll_interval,
        "schedule": schedule,
        "cleanup": "cleanup" in form,
        "include_stopped": "include_stopped" in form,
        "revive_stopped": "revive_stopped" in form,
        "monitor_only": "monitor_only" in form,
        "label_enable": "label_enable" in form,
        "log_level": log_level,
        "no_startup_message": "no_startup_message" in form,
        "timeout": timeout,
        "notifications_discord": "notifications_discord" in form,
        # An empty field keeps the existing secret because it is never rendered
        # back into the settings page.
        "discord_webhook_url": (
            submitted_webhook
            or current_settings.get("discord_webhook_url", "")
        ),
        "nextcloud_post_update_enabled": nextcloud_enabled,
        "nextcloud_post_update_commands": nextcloud_commands,
    }

    _write_settings(settings)

    return errors
