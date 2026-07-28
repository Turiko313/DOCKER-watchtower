import base64
import hashlib
import hmac
import os
import secrets

from flask import Flask, Response, flash, jsonify, redirect, render_template, request, url_for
import requests as http_requests

from docker_helpers import get_update_statuses, list_containers
from settings import load_settings, save_settings
from watchtower_api import (
    WATCHTOWER_API_TOKEN,
    WATCHTOWER_API_URL,
    get_watchtower_metrics,
    reset_metrics,
    restart_watchtower,
)


def _read_required_secret(name):
    """Load a required secret from NAME or NAME_FILE."""
    file_path = os.environ.get(f"{name}_FILE", "").strip()
    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as secret_file:
                value = secret_file.read().strip()
        except OSError as exc:
            raise RuntimeError(f"Impossible de lire {name}_FILE") from exc
    else:
        value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} est obligatoire")
    return value


app = Flask(__name__)
_secret_key = _read_required_secret("SECRET_KEY")
if len(_secret_key) < 32:
    raise RuntimeError("SECRET_KEY doit contenir au moins 32 caracteres")
app.config.update(MAX_CONTENT_LENGTH=64 * 1024, SECRET_KEY=_secret_key)

DASHBOARD_USERNAME = _read_required_secret("DASHBOARD_USERNAME")
DASHBOARD_PASSWORD = _read_required_secret("DASHBOARD_PASSWORD")
if ":" in DASHBOARD_USERNAME:
    raise RuntimeError("DASHBOARD_USERNAME ne doit pas contenir ':'")
if len(DASHBOARD_PASSWORD) < 12:
    raise RuntimeError("DASHBOARD_PASSWORD doit contenir au moins 12 caracteres")

# The token is deterministic across Gunicorn workers, but cannot be guessed
# without SECRET_KEY. Rotating SECRET_KEY invalidates it immediately.
_csrf_digest = hmac.new(
    app.secret_key.encode("utf-8"),
    b"watchtower-dashboard-csrf-v1",
    hashlib.sha256,
).digest()
CSRF_TOKEN = base64.urlsafe_b64encode(_csrf_digest).decode("ascii").rstrip("=")
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _check_auth(auth):
    if not auth:
        return False
    username_matches = secrets.compare_digest(
        auth.username or "", DASHBOARD_USERNAME
    )
    password_matches = secrets.compare_digest(
        auth.password or "", DASHBOARD_PASSWORD
    )
    return username_matches & password_matches


def _auth_failed_response():
    return Response(
        "Authentification requise",
        401,
        {
            "WWW-Authenticate": 'Basic realm="Watchtower Dashboard", charset="UTF-8"',
            "Cache-Control": "no-store",
        },
    )


def _csrf_valid():
    token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    return bool(token and secrets.compare_digest(token, CSRF_TOKEN))


@app.before_request
def _protect_request():
    if not _check_auth(request.authorization):
        return _auth_failed_response()
    if request.method in _WRITE_METHODS and not _csrf_valid():
        return jsonify({"status": "error", "message": "Jeton CSRF invalide."}), 403
    return None


@app.after_request
def _security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'none'; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.context_processor
def _inject_security_context():
    return {"csrf_token": CSRF_TOKEN}


@app.route("/")
def dashboard():
    containers = list_containers()
    metrics = get_watchtower_metrics()
    update_statuses = get_update_statuses()
    grouped = {}
    for container in containers:
        grouped.setdefault(container["image"], []).append(container)
    return render_template(
        "dashboard.html",
        containers=containers,
        metrics=metrics,
        update_statuses=update_statuses,
        grouped=grouped,
    )


@app.route("/update", methods=["POST"])
def trigger_update():
    try:
        response = http_requests.post(
            f"{WATCHTOWER_API_URL}/v1/update",
            headers={"Authorization": f"Bearer {WATCHTOWER_API_TOKEN}"},
            timeout=120,
        )
        if response.status_code == 200:
            return jsonify(
                {"status": "success", "message": "Mise a jour declenchee avec succes."}
            )
        app.logger.warning("Watchtower update API returned HTTP %s", response.status_code)
        return jsonify(
            {
                "status": "error",
                "message": f"Watchtower a repondu avec le code {response.status_code}.",
            }
        ), 502
    except http_requests.RequestException:
        app.logger.exception("Unable to reach the Watchtower update API")
        return jsonify(
            {
                "status": "error",
                "message": "Impossible de contacter le service Watchtower.",
            }
        ), 502


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        errors = save_settings(request.form)
        for error in errors:
            flash(error, "error")
        restarted = restart_watchtower()
        if restarted and not errors:
            flash("Parametres sauvegardes. Watchtower redemarre.", "success")
        elif restarted:
            flash("Parametres sauvegardes avec erreurs. Watchtower redemarre.", "success")
        return redirect(url_for("settings"))

    current = load_settings()
    # Never send the Discord webhook token back to the browser.
    current["discord_webhook_configured"] = bool(current.get("discord_webhook_url"))
    current["discord_webhook_url"] = ""
    return render_template("settings.html", settings=current)


@app.route("/reset_metrics", methods=["POST"])
def reset_metrics_route():
    reset_metrics()
    flash("Metriques reinitialisees.", "success")
    return redirect(url_for("settings"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
