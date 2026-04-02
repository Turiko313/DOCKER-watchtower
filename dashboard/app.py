import os
import json
import re
import time
import secrets
import functools
import subprocess
from collections import defaultdict

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import Flask, render_template, request, redirect, url_for, flash
import docker
import requests as http_requests

# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------
app = Flask(__name__)

_provided_key = os.environ.get("SECRET_KEY", "").strip()
if not _provided_key:
    _provided_key = secrets.token_hex(32)
    app.logger.warning(
        "SECRET_KEY non definie ! Une cle aleatoire a ete generee. "
        "Les sessions seront perdues au redemarrage. "
        "Definissez SECRET_KEY dans votre fichier .env."
    )
app.secret_key = _provided_key

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
# Signed-cookie authentication (session-independent, like FileBrowser)
# ---------------------------------------------------------------------------
AUTH_COOKIE = "auth_token"
AUTH_MAX_AGE = 30 * 24 * 3600  # 30 days

_auth_serializer = URLSafeTimedSerializer(app.secret_key)


def _create_auth_token(username):
    """Create a signed auth token for the given username."""
    return _auth_serializer.dumps({"u": username})


def _verify_auth_token(token):
    """Verify a signed auth token.  Returns True if valid and not expired."""
    try:
        data = _auth_serializer.loads(token, max_age=AUTH_MAX_AGE)
        return data.get("u") == DASHBOARD_USERNAME
    except (BadSignature, SignatureExpired):
        return False


def _is_logged_in():
    """Check if the current request has a valid auth cookie."""
    token = request.cookies.get(AUTH_COOKIE)
    return bool(token) and _verify_auth_token(token)


@app.context_processor
def _inject_auth():
    """Make 'logged_in' available in all templates."""
    return {"logged_in": _is_logged_in()}


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not _is_logged_in():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Rate limiting on /login (in-memory, per IP)
# ---------------------------------------------------------------------------
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 60
_login_attempts: dict[str, list[float]] = defaultdict(list)


def _is_rate_limited(ip: str) -> bool:
    """Return True if *ip* has exceeded the login attempt limit."""
    now = time.time()
    cutoff = now - _LOGIN_WINDOW_SECONDS
    _login_attempts[ip] = [t for t in _login_attempts[ip] if t > cutoff]
    return len(_login_attempts[ip]) >= _LOGIN_MAX_ATTEMPTS


def _record_login_attempt(ip: str) -> None:
    _login_attempts[ip].append(time.time())


# ---------------------------------------------------------------------------
# Docker client (socket mounted from host)
# ---------------------------------------------------------------------------
docker_client = docker.from_env(version="auto")


def _get_docker_client():
    """Return a working Docker client, reconnecting if stale."""
    global docker_client
    try:
        docker_client.ping()
    except Exception:
        docker_client = docker.from_env(version="auto")
    return docker_client


# ===========================================================================
# Routes
# ===========================================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if _is_logged_in():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
        if _is_rate_limited(client_ip):
            flash("Trop de tentatives. Reessayez dans une minute.", "error")
            return render_template("login.html")
        _record_login_attempt(client_ip)
        if (request.form.get("username") == DASHBOARD_USERNAME
                and request.form.get("password") == DASHBOARD_PASSWORD):
            _login_attempts.pop(client_ip, None)
            resp = redirect(url_for("dashboard"))
            resp.set_cookie(
                AUTH_COOKIE,
                _create_auth_token(DASHBOARD_USERNAME),
                max_age=AUTH_MAX_AGE,
                httponly=True,
                samesite="Lax",
            )
            return resp
        flash("Identifiants incorrects.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    resp = redirect(url_for("login"))
    resp.delete_cookie(AUTH_COOKIE)
    return resp


@app.route("/")
@login_required
def dashboard():
    containers = _list_containers()
    metrics = _get_watchtower_metrics()
    update_statuses = _get_update_statuses()
    grouped = {}
    for c in containers:
        grouped.setdefault(c["image"], []).append(c)
    return render_template("dashboard.html", containers=containers, metrics=metrics, update_statuses=update_statuses, grouped=grouped)


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
            flash("Mise à jour déclenchée avec succès.", "success")
        else:
            flash(f"Watchtower a répondu avec le code {resp.status_code}.", "error")
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
            flash("Paramètres sauvegardés. Watchtower redémarré.", "success")
        return redirect(url_for("settings"))
    current = _load_settings()
    return render_template("settings.html", settings=current)

# ===========================================================================
# Container helpers
# ===========================================================================
def _list_containers():
    containers = []
    try:
        client = _get_docker_client()
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
                # Docker returns "0001-01-01T00:00:00Z" when never finished
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
                    app.logger.warning("Skipping container: %s", inner_exc)
    except Exception as exc:
        flash(f"Erreur Docker : {exc}", "error")

    containers.sort(key=lambda x: x["name"])
    return containers


# ===========================================================================
# Update statuses from watchtower logs (last 24 h)
# ===========================================================================
def _get_update_statuses():
    """Parse watchtower logs (last 24 h) for updated / failed containers."""
    statuses = {}
    try:
        client = _get_docker_client()
        wt = client.containers.get("watchtower-dashboard")
        logs = wt.logs(since=int(time.time()) - 86400, stdout=True, stderr=True)
        for line in logs.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            # Watchtower uses logrus; lines look like:
            #   time="..." level=info msg="Creating /container-name"
            m = re.search(r'Creating /([^"]+)', line)
            if m:
                name = m.group(1).strip()
                if name:
                    statuses[name] = "updated"
            elif "Unable to update container" in line:
                # msg="Unable to update container \"/name\": err"
                m = re.search(r'Unable to update container.*?/([^"\\]+)', line)
                if m:
                    name = m.group(1).strip()
                    if name:
                        statuses[name] = "failed"
    except Exception:
        pass
    return statuses


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
    "no_startup_message": True,
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
            # Verify watchtower is actually running after restart
            time.sleep(2)
            check = subprocess.run(
                ["supervisorctl", "status", "watchtower"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if "RUNNING" in check.stdout:
                return True
            else:
                flash(f"Watchtower redémarré mais statut inattendu : {check.stdout.strip()}", "error")
                return False
        else:
            flash(f"Erreur supervisorctl : {result.stderr}", "error")
            return False
    except Exception as exc:
        flash(f"Erreur lors du redémarrage de Watchtower : {exc}", "error")
        return False


# ===========================================================================
# Entry point (development only — production uses gunicorn via supervisord)
# ===========================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
