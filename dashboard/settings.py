import os
import json

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
            settings.update(json.load(fh))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return settings

def save_settings(form):
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

    settings = {
        "poll_interval": poll_interval,
        "schedule": form.get("schedule", "").strip(),
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
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w") as fh:
        json.dump(settings, fh, indent=2)
