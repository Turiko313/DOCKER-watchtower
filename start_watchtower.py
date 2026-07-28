#!/usr/bin/env python3
"""Read /config/watchtower.json settings, set env vars, then exec watchtower."""

import base64
import json
import os
import sys
import tempfile

SETTINGS_FILE = "/config/watchtower.json"

BOOL_SETTINGS = {
    "cleanup": "WATCHTOWER_CLEANUP",
    "include_stopped": "WATCHTOWER_INCLUDE_STOPPED",
    "revive_stopped": "WATCHTOWER_REVIVE_STOPPED",
    "monitor_only": "WATCHTOWER_MONITOR_ONLY",
    "label_enable": "WATCHTOWER_LABEL_ENABLE",
    "no_startup_message": "WATCHTOWER_NO_STARTUP_MESSAGE",
}

# Rolling restarts are intentionally unsupported by this dashboard. Clear a
# legacy container environment value even when the settings file is absent or
# invalid, so it cannot abort an update cycle because of container dependencies.
os.environ.pop("WATCHTOWER_ROLLING_RESTART", None)

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

    # Discord notifications via shoutrrr. Clear both variables first so an
    # invalid or disabled setting cannot inherit a stale environment value.
    os.environ.pop("WATCHTOWER_NOTIFICATIONS", None)
    os.environ.pop("WATCHTOWER_NOTIFICATION_URL", None)
    if s.get("notifications_discord") and s.get("discord_webhook_url"):
        url = s["discord_webhook_url"].strip()
        if "/api/webhooks/" in url:
            parts = url.split("/api/webhooks/")[-1].strip("/").split("/")
            if len(parts) == 2:
                os.environ["WATCHTOWER_NOTIFICATIONS"] = "shoutrrr"
                os.environ["WATCHTOWER_NOTIFICATION_URL"] = f"discord://{parts[1]}@{parts[0]}"
                print("[start_watchtower] Discord notifications enabled.", file=sys.stderr)

    print(f"[start_watchtower] Settings loaded from {SETTINGS_FILE}", file=sys.stderr)
except Exception as exc:
    print(f"[start_watchtower] Warning: could not load settings: {exc}", file=sys.stderr)
    # Safe defaults so watchtower always has periodic checking
    os.environ.setdefault("WATCHTOWER_POLL_INTERVAL", "86400")
    os.environ.setdefault("WATCHTOWER_NO_STARTUP_MESSAGE", "true")
    print("[start_watchtower] Fallback: poll interval 86400s", file=sys.stderr)

# CRITICAL: Since we use WATCHTOWER_HTTP_API_UPDATE=true, Watchtower normally
# disables background polling. We MUST force periodic polls back on.
os.environ["WATCHTOWER_HTTP_API_PERIODIC_POLLS"] = "true"

# ---------------------------------------------------------------------------
# GHCR private registry authentication
# ---------------------------------------------------------------------------
ghcr_user = os.environ.get("GHCR_USERNAME", "").strip()
ghcr_token = os.environ.get("GHCR_TOKEN", "").strip()
ghcr_token_file = os.environ.get("GHCR_TOKEN_FILE", "").strip()

if ghcr_token_file:
    try:
        with open(ghcr_token_file, "r", encoding="utf-8") as token_file:
            ghcr_token = token_file.read().strip()
    except OSError as exc:
        print(
            f"[start_watchtower] Warning: could not read GHCR_TOKEN_FILE: {exc}",
            file=sys.stderr,
        )

if ghcr_user and ghcr_token:
    docker_cfg_dir = "/config/docker-config"
    os.makedirs(docker_cfg_dir, mode=0o700, exist_ok=True)
    os.chmod(docker_cfg_dir, 0o700)
    auth_str = base64.b64encode(f"{ghcr_user}:{ghcr_token}".encode()).decode()
    cfg = {"auths": {"ghcr.io": {"auth": auth_str}}}
    fd, temporary_file = tempfile.mkstemp(dir=docker_cfg_dir, prefix=".config-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as config_file:
            json.dump(cfg, config_file)
            config_file.write("\n")
            config_file.flush()
            os.fsync(config_file.fileno())
        os.replace(temporary_file, os.path.join(docker_cfg_dir, "config.json"))
    except OSError:
        try:
            os.unlink(temporary_file)
        except FileNotFoundError:
            pass
        raise
    os.environ["DOCKER_CONFIG"] = docker_cfg_dir
    print("[start_watchtower] GHCR auth configured.", file=sys.stderr)
elif ghcr_user or ghcr_token:
    print(
        "[start_watchtower] Warning: incomplete GHCR credentials; anonymous access will be used.",
        file=sys.stderr,
    )
else:
    print("[start_watchtower] GHCR anonymous access enabled.", file=sys.stderr)

os.execv("/usr/local/bin/watchtower", ["/usr/local/bin/watchtower"])
