#!/usr/bin/env python3
"""Read /config/watchtower.json settings, set env vars, then exec watchtower."""

import json
import os
import sys

SETTINGS_FILE = "/config/watchtower.json"

BOOL_SETTINGS = {
    "cleanup": "WATCHTOWER_CLEANUP",
    "include_stopped": "WATCHTOWER_INCLUDE_STOPPED",
    "revive_stopped": "WATCHTOWER_REVIVE_STOPPED",
    "monitor_only": "WATCHTOWER_MONITOR_ONLY",
    "label_enable": "WATCHTOWER_LABEL_ENABLE",
    "rolling_restart": "WATCHTOWER_ROLLING_RESTART",
    "no_startup_message": "WATCHTOWER_NO_STARTUP_MESSAGE",
}

try:
    with open(SETTINGS_FILE) as f:
        s = json.load(f)

    # Schedule or poll interval
    if s.get("schedule"):
        os.environ["WATCHTOWER_SCHEDULE"] = s["schedule"]
    else:
        os.environ["WATCHTOWER_POLL_INTERVAL"] = s.get("poll_interval", "86400")

    # Boolean flags
    for key, env_var in BOOL_SETTINGS.items():
        if s.get(key):
            os.environ[env_var] = "true"

    # Log level and timeout
    if s.get("log_level"):
        os.environ["WATCHTOWER_LOG_LEVEL"] = s["log_level"]
    if s.get("timeout"):
        os.environ["WATCHTOWER_TIMEOUT"] = s["timeout"]

    # Discord notifications via shoutrrr
    if s.get("notifications_discord") and s.get("discord_webhook_url"):
        url = s["discord_webhook_url"].strip()
        if "/api/webhooks/" in url:
            parts = url.split("/api/webhooks/")[-1].strip("/").split("/")
            if len(parts) == 2:
                os.environ["WATCHTOWER_NOTIFICATIONS"] = "shoutrrr"
                os.environ["WATCHTOWER_NOTIFICATION_URL"] = f"discord://{parts[1]}@{parts[0]}"
except (FileNotFoundError, json.JSONDecodeError, Exception) as exc:
    print(f"[start_watchtower] Warning: could not load settings: {exc}", file=sys.stderr)

os.execv("/usr/local/bin/watchtower", ["/usr/local/bin/watchtower"])
