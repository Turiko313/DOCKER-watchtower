"""
Watchtower Dashboard – Flask web application.

Provides a password-protected web interface that displays:
  - Status of all Docker containers
  - Watchtower operational status
  - Update history (from Watchtower container logs)
  - Manual update trigger via Watchtower HTTP API
"""

import os
import re
import secrets
from datetime import datetime, timezone
from functools import wraps

import docker
import requests
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
_raw_password = os.environ.get("DASHBOARD_PASSWORD")
if not _raw_password:
    raise RuntimeError(
        "DASHBOARD_PASSWORD environment variable is not set. "
        "Please define a strong password in your .env file."
    )
PASSWORD_HASH = generate_password_hash(_raw_password)

WATCHTOWER_API_URL = os.environ.get("WATCHTOWER_API_URL", "http://watchtower:8080")
WATCHTOWER_API_TOKEN = os.environ.get("WATCHTOWER_API_TOKEN", "")

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def login_required(f):
    """Decorator that redirects to login if the user is not authenticated."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------


def get_docker_client():
    """Return a Docker SDK client connected to the local socket."""
    return docker.from_env()


def _uptime_string(started_at: str) -> str:
    """Convert an ISO-8601 timestamp to a human-readable uptime string."""
    try:
        # Docker timestamps end with 'Z' or a timezone offset; normalise to UTC
        ts = re.sub(r"\.\d+", "", started_at.rstrip("Z"))
        start = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - start
        days = delta.days
        hours, rem = divmod(delta.seconds, 3600)
        minutes, _ = divmod(rem, 60)
        if days > 0:
            return f"{days}j {hours}h {minutes}m"
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return "Inconnu"


def get_containers():
    """Return a list of dicts with information about all Docker containers."""
    client = get_docker_client()
    result = []
    for c in client.containers.list(all=True):
        tags = c.image.tags
        image_name = tags[0] if tags else c.image.short_id
        started_at = c.attrs.get("State", {}).get("StartedAt", "")
        uptime = _uptime_string(started_at) if c.status == "running" else "—"
        result.append(
            {
                "name": c.name,
                "image": image_name,
                "status": c.status,
                "uptime": uptime,
                "created": c.attrs.get("Created", "")[:10],
            }
        )
    result.sort(key=lambda x: x["name"])
    return result


def get_watchtower_info():
    """Return status information for the Watchtower container."""
    try:
        client = get_docker_client()
        wt = client.containers.get("watchtower")
        started_at = wt.attrs.get("State", {}).get("StartedAt", "")
        return {
            "running": wt.status == "running",
            "status": wt.status,
            "uptime": _uptime_string(started_at) if wt.status == "running" else "—",
        }
    except docker.errors.NotFound:
        return {"running": False, "status": "introuvable", "uptime": "—"}
    except Exception:
        return {"running": False, "status": "erreur", "uptime": "—"}


def get_update_history(max_lines: int = 50):
    """Parse Watchtower container logs and return update-related lines."""
    history = []
    try:
        client = get_docker_client()
        wt = client.containers.get("watchtower")
        raw = wt.logs(tail=200, timestamps=True).decode("utf-8", errors="replace")
        keywords = ("Updated", "updated", "Stopping", "Starting", "Found", "Pulling")
        for line in raw.splitlines():
            if any(kw in line for kw in keywords):
                # Strip ANSI escape codes
                clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
                history.append(clean)
    except Exception:
        pass
    return history[-max_lines:]


# ---------------------------------------------------------------------------
# Watchtower API helpers
# ---------------------------------------------------------------------------


def _wt_headers():
    return {"Authorization": f"Bearer {WATCHTOWER_API_TOKEN}"}


def trigger_update():
    """Ask Watchtower to perform an immediate update check."""
    try:
        resp = requests.get(
            f"{WATCHTOWER_API_URL}/v1/update",
            headers=_wt_headers(),
            timeout=10,
        )
        return resp.status_code == 200, resp.text
    except Exception as exc:
        return False, str(exc)


def get_watchtower_metrics():
    """Fetch Prometheus metrics from Watchtower and return a dict of key values."""
    metrics = {}
    try:
        resp = requests.get(
            f"{WATCHTOWER_API_URL}/v1/metrics",
            headers=_wt_headers(),
            timeout=5,
        )
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.split()
                if len(parts) == 2:
                    metrics[parts[0]] = parts[1]
    except Exception:
        pass
    return metrics


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page."""
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == USERNAME and check_password_hash(PASSWORD_HASH, password):
            session["logged_in"] = True
            session.permanent = False
            return redirect(url_for("index"))
        error = "Nom d'utilisateur ou mot de passe incorrect."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    """Clear session and redirect to login."""
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    """Main dashboard page."""
    containers = get_containers()
    watchtower = get_watchtower_info()
    history = get_update_history()
    metrics = get_watchtower_metrics()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    running_count = sum(1 for c in containers if c["status"] == "running")
    stopped_count = len(containers) - running_count

    # Extract relevant metrics
    scanned = metrics.get("watchtower_containers_scanned", "—")
    updated = metrics.get("watchtower_containers_updated", "—")
    failed = metrics.get("watchtower_containers_failed", "—")

    return render_template(
        "dashboard.html",
        containers=containers,
        watchtower=watchtower,
        history=history,
        now=now,
        running_count=running_count,
        stopped_count=stopped_count,
        scanned=scanned,
        updated=updated,
        failed=failed,
    )


@app.route("/api/trigger", methods=["POST"])
@login_required
def api_trigger():
    """Trigger an immediate Watchtower update check (AJAX endpoint)."""
    success, message = trigger_update()
    return jsonify({"success": success, "message": message})


@app.route("/api/status")
@login_required
def api_status():
    """Return a JSON snapshot of container states (for live refresh)."""
    containers = get_containers()
    watchtower = get_watchtower_info()
    return jsonify({"containers": containers, "watchtower": watchtower})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
