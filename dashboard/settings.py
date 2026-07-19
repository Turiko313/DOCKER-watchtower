import os
import json
import tempfile

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
    "rolling_restart": False,
    "log_level": "info",
    "no_startup_message": True,
    "timeout": "30",
    "notifications_discord": False,
    "discord_webhook_url": "",
}

def load_settings():
    settings = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r") as fh:
            saved_settings = json.load(fh)
        if isinstance(saved_settings, dict):
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

    try:
        poll_interval = str(max(60, int(form.get("poll_interval") or 86400)))
    except (ValueError, TypeError):
        poll_interval = "86400"

    try:
        timeout = str(max(10, int(form.get("timeout") or 30)))
    except (ValueError, TypeError):
        timeout = "30"

    log_level = form.get("log_level", "info")
    if log_level not in ("debug", "info", "warn", "error", "fatal", "panic"):
        log_level = "info"

    # Validation du format Cron (Watchtower exige 6 champs ou des macros type @daily)
    schedule = form.get("schedule", "").strip()
    if schedule and not schedule.startswith("@"):
        parts = schedule.split()
        if len(parts) != 6:
            errors.append("Le format cron est invalide. Il doit contenir exactement 6 champs. La planification cron a ete desactivee.")
            schedule = ""

    settings = {
        "poll_interval": poll_interval,
        "schedule": schedule,
        "cleanup": "cleanup" in form,
        "include_stopped": "include_stopped" in form,
        "revive_stopped": "revive_stopped" in form,
        "monitor_only": "monitor_only" in form,
        "label_enable": "label_enable" in form,
        "rolling_restart": "rolling_restart" in form,
        "log_level": log_level,
        "no_startup_message": "no_startup_message" in form,
        "timeout": timeout,
        "notifications_discord": "notifications_discord" in form,
        "discord_webhook_url": form.get("discord_webhook_url", "").strip(),
    }

    _write_settings(settings)

    return errors
