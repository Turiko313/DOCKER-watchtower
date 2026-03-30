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

    # Schedule or poll interval (mutually exclusive)
    schedule = " ".join(s.get("schedule", "").split())
    if schedule:
        os.environ["WATCHTOWER_SCHEDULE"] = schedule
        os.environ.pop("WATCHTOWER_POLL_INTERVAL", None)
        print(f"[start_watchtower] Using schedule: {schedule}", file=sys.stderr)
    else:
        os.environ.pop("WATCHTOWER_SCHEDULE", None)
        try:
            poll = str(max(60, int(s.get("poll_interval", 86400))))
        except (ValueError, TypeError):
            poll = "86400"
        os.environ["WATCHTOWER_POLL_INTERVAL"] = poll
        print(f"[start_watchtower] Using poll interval: {poll}s", file=sys.stderr)

    # Boolean flags (explicitly clean when False)
    for key, env_var in BOOL_SETTINGS.items():
        if s.get(key):
            os.environ[env_var] = "true"
        else:
            os.environ.pop(env_var, None)

    # Log level (validated)
    log_level = s.get("log_level", "info")
    if log_level in ("debug", "info", "warn", "error", "fatal", "panic"):
        os.environ["WATCHTOWER_LOG_LEVEL"] = log_level

    # Timeout (validated)
    try:
        timeout = str(max(10, int(s.get("timeout", 30))))
    except (ValueError, TypeError):
        timeout = "30"
    os.environ["WATCHTOWER_TIMEOUT"] = timeout

    # Discord notifications via shoutrrr
    if s.get("notifications_discord") and s.get("discord_webhook_url"):
        url = s["discord_webhook_url"].strip()
        if "/api/webhooks/" in url:
            parts = url.split("/api/webhooks/")[-1].strip("/").split("/")
            if len(parts) == 2:
                os.environ["WATCHTOWER_NOTIFICATIONS"] = "shoutrrr"
                os.environ["WATCHTOWER_NOTIFICATION_URL"] = f"discord://{parts[1]}@{parts[0]}"
                print("[start_watchtower] Discord notifications enabled.", file=sys.stderr)
    else:
        os.environ.pop("WATCHTOWER_NOTIFICATIONS", None)
        os.environ.pop("WATCHTOWER_NOTIFICATION_URL", None)

    print(f"[start_watchtower] Settings loaded from {SETTINGS_FILE}", file=sys.stderr)
except (FileNotFoundError, json.JSONDecodeError, Exception) as exc:
    print(f"[start_watchtower] Warning: could not load settings: {exc}", file=sys.stderr)
    # Safe defaults so watchtower always has periodic checking
    os.environ.setdefault("WATCHTOWER_POLL_INTERVAL", "86400")
    print("[start_watchtower] Fallback: poll interval 86400s", file=sys.stderr)

# ---------------------------------------------------------------------------
# GHCR private registry authentication
# ---------------------------------------------------------------------------
import base64

ghcr_user = os.environ.get("GHCR_USERNAME", "").strip()
ghcr_token = os.environ.get("GHCR_TOKEN", "").strip()

if ghcr_user and ghcr_token:
    docker_cfg_dir = "/config/docker-config"
    os.makedirs(docker_cfg_dir, exist_ok=True)
    auth_str = base64.b64encode(f"{ghcr_user}:{ghcr_token}".encode()).decode()
    cfg = {"auths": {"ghcr.io": {"auth": auth_str}}}
    with open(os.path.join(docker_cfg_dir, "config.json"), "w") as f:
        json.dump(cfg, f)
    os.environ["DOCKER_CONFIG"] = docker_cfg_dir
    print("[start_watchtower] GHCR auth configured.", file=sys.stderr)

os.execv("/usr/local/bin/watchtower", ["/usr/local/bin/watchtower"])
