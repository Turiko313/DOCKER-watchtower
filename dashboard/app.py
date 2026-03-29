import os
import json
import time
import secrets
import functools
import subprocess

from flask import Flask, render_template, request, redirect, url_for, session, flash, make_response
import docker
import requests as http_requests

# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "dev-secret-key"

REMEMBER_DAYS = 30
REMEMBER_SECONDS = REMEMBER_DAYS * 24 * 3600

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "admin")
WATCHTOWER_API_TOKEN = os.environ.get("WATCHTOWER_HTTP_API_TOKEN", "")
WATCHTOWER_API_URL = os.environ.get("WATCHTOWER_API_URL", "http://localhost:8080")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "watchtower.json")

# ---------------------------------------------------------------------------
# Docker client (socket mounted from host)
# ---------------------------------------------------------------------------
docker_client = docker.from_env(version="auto")


# ===========================================================================
# Remember-me token helpers (server-side, persisted on /config volume)
# ===========================================================================
REMEMBER_FILE = os.path.join(CONFIG_DIR, "remember_tokens.json")


def _load_remember_tokens():
    try:
        with open(REMEMBER_FILE, "r") as fh:
            tokens = json.load(fh)
        now = time.time()
        tokens = {k: v for k, v in tokens.items() if v > now}
        return tokens
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_remember_tokens(tokens):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(REMEMBER_FILE, "w") as fh:
        json.dump(tokens, fh)


def _create_remember_token():
    token = secrets.token_hex(32)
    tokens = _load_remember_tokens()
    tokens[token] = time.time() + REMEMBER_SECONDS
    _save_remember_tokens(tokens)
    return token


def _validate_remember_token(token):
    if not token:
        return False
    tokens = _load_remember_tokens()
    return token in tokens and tokens[token] > time.time()


def _delete_remember_token(token):
    if not token:
        return
    tokens = _load_remember_tokens()
    tokens.pop(token, None)
    _save_remember_tokens(tokens)


# ===========================================================================
# Auth helpers
# ===========================================================================
def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            token = request.cookies.get("remember_token")
            if _validate_remember_token(token):
                session["logged_in"] = True
            else:
                return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ===========================================================================
# Routes
# ===========================================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if (request.form.get("username") == DASHBOARD_USERNAME
                and request.form.get("password") == DASHBOARD_PASSWORD):
            session["logged_in"] = True
            resp = redirect(url_for("dashboard"))
            if request.form.get("remember_me"):
                token = _create_remember_token()
                resp.set_cookie(
                    "remember_token", token,
                    max_age=REMEMBER_SECONDS,
                    httponly=True,
                    samesite="Lax",
                )
            return resp
        flash("Identifiants incorrects.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    _delete_remember_token(request.cookies.get("remember_token"))
    session.clear()
    resp = redirect(url_for("login"))
    resp.delete_cookie("remember_token")
    return resp


@app.route("/")
@login_required
def dashboard():
    containers = _list_containers()
    metrics = _get_watchtower_metrics()
    return render_template("dashboard.html", containers=containers, metrics=metrics)


@app.route("/update", methods=["POST"])
@login_required
def trigger_update():
    try:
        resp = http_requests.post(
            f"{WATCHTOWER_API_URL}/v1/update",
            headers={"Authorization": f"Bearer {WATCHTOWER_API_TOKEN}"},
            timeout=120,
        )
        if resp.status_code == 200:
            flash("Mise a jour declenchee avec succes.", "success")
        else:
            flash(f"Watchtower a repondu avec le code {resp.status_code}.", "error")
    except Exception as exc:
        flash(f"Impossible de contacter Watchtower : {exc}", "error")
    return redirect(url_for("dashboard"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        _save_settings(request.form)
        ok = _restart_watchtower()
        if ok:
            flash("Parametres sauvegardes. Watchtower redemarre.", "success")
        return redirect(url_for("settings"))
    current = _load_settings()
    return render_template("settings.html", settings=current)


# ===========================================================================
# Container helpers
# ===========================================================================
def _list_containers():
    containers = []
    try:
        for c in docker_client.containers.list(all=True):
            try:
                image_name = c.image.tags[0] if c.image.tags else c.image.short_id
            except Exception:
                image_name = c.attrs.get("Config", {}).get("Image", "unknown")
            wt_label = c.labels.get("com.centurylinklabs.watchtower.enable")
            if wt_label is None:
                wt_enabled = True
            else:
                wt_enabled = wt_label.lower() != "false"
            containers.append({
                "name": c.name,
                "image": image_name,
                "status": c.status,
                "id": c.short_id,
                "watchtower_enabled": wt_enabled,
                "created": c.attrs.get("Created", "")[:19].replace("T", " "),
            })
    except Exception as exc:
        flash(f"Erreur Docker : {exc}", "error")
    containers.sort(key=lambda x: x["name"])
    return containers


# ===========================================================================
# Watchtower metrics
# ===========================================================================
def _get_watchtower_metrics():
    try:
        resp = http_requests.get(
            f"{WATCHTOWER_API_URL}/v1/metrics",
            headers={"Authorization": f"Bearer {WATCHTOWER_API_TOKEN}"},
            timeout=5,
        )
        if resp.status_code == 200:
            return _parse_prometheus(resp.text)
    except Exception:
        pass
    return {}


def _parse_prometheus(text):
    metrics = {}
    for line in text.strip().splitlines():
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            metrics[parts[0]] = parts[1]
    return metrics


# ===========================================================================
# Settings persistence
# ===========================================================================
_DEFAULTS = {
    "poll_interval": "86400",
    "schedule": "",
    "cleanup": True,
    "include_stopped": False,
    "revive_stopped": False,
    "monitor_only": False,
    "label_enable": False,
    "rolling_restart": False,
    "log_level": "info",
    "no_startup_message": False,
    "timeout": "30",
    "notifications_discord": False,
    "discord_webhook_url": "",
}


def _load_settings():
    settings = dict(_DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r") as fh:
            settings.update(json.load(fh))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return settings


def _save_settings(form):
    settings = {
        "poll_interval": form.get("poll_interval", "86400"),
        "schedule": form.get("schedule", "").strip(),
        "cleanup": "cleanup" in form,
        "include_stopped": "include_stopped" in form,
        "revive_stopped": "revive_stopped" in form,
        "monitor_only": "monitor_only" in form,
        "label_enable": "label_enable" in form,
        "rolling_restart": "rolling_restart" in form,
        "log_level": form.get("log_level", "info"),
        "no_startup_message": "no_startup_message" in form,
        "timeout": form.get("timeout", "30"),
        "notifications_discord": "notifications_discord" in form,
        "discord_webhook_url": form.get("discord_webhook_url", "").strip(),
    }
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w") as fh:
        json.dump(settings, fh, indent=2)


# ===========================================================================
# Watchtower restart via supervisord
# ===========================================================================
def _restart_watchtower():
    try:
        result = subprocess.run(
            ["supervisorctl", "restart", "watchtower"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True
        else:
            flash(f"Erreur supervisorctl : {result.stderr}", "error")
            return False
    except Exception as exc:
        flash(f"Erreur lors du redemarrage de Watchtower : {exc}", "error")
        return False


# ===========================================================================
# Entry point
# ===========================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
