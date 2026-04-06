import os
import time
import secrets
import functools
from collections import defaultdict

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import requests as http_requests

from docker_helpers import list_containers, get_update_statuses
from settings import load_settings, save_settings
from watchtower_api import get_watchtower_metrics, reset_metrics, restart_watchtower, WATCHTOWER_API_URL, WATCHTOWER_API_TOKEN

# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------
app = Flask(__name__)

_provided_key = os.environ.get("SECRET_KEY", "").strip()
if not _provided_key:
    _provided_key = secrets.token_hex(32)
    app.logger.warning("SECRET_KEY non definie ! Une cle aleatoire a ete generee.")
app.secret_key = _provided_key

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "admin")

# ---------------------------------------------------------------------------
# Signed-cookie authentication (session-independent, like FileBrowser)
# ---------------------------------------------------------------------------
AUTH_COOKIE = "auth_token"
AUTH_MAX_AGE = 30 * 24 * 3600  # 30 days

_auth_serializer = URLSafeTimedSerializer(app.secret_key)

def _create_auth_token(username):
    return _auth_serializer.dumps({"u": username})

def _verify_auth_token(token):
    try:
        data = _auth_serializer.loads(token, max_age=AUTH_MAX_AGE)
        return data.get("u") == DASHBOARD_USERNAME
    except (BadSignature, SignatureExpired):
        return False

def _is_logged_in():
    token = request.cookies.get(AUTH_COOKIE)
    return bool(token) and _verify_auth_token(token)

@app.context_processor
def _inject_auth():
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
    now = time.time()
    cutoff = now - _LOGIN_WINDOW_SECONDS
    _login_attempts[ip] = [t for t in _login_attempts[ip] if t > cutoff]
    return len(_login_attempts[ip]) >= _LOGIN_MAX_ATTEMPTS

def _record_login_attempt(ip: str) -> None:
    _login_attempts[ip].append(time.time())

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
    containers = list_containers()
    metrics = get_watchtower_metrics()
    update_statuses = get_update_statuses()
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
            return jsonify({"status": "success", "message": "Mise a jour declenchee avec succes."})
        else:
            return jsonify({"status": "error", "message": f"Watchtower a repondu avec le code {resp.status_code}."}), 500
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Impossible de contacter Watchtower : {exc}"}), 500

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        errors = save_settings(request.form)
        for err in errors:
            flash(err, "error")
        ok = restart_watchtower()
        if ok and not errors:
            flash("Parametres sauvegardes. Watchtower redemarre.", "success")
        elif ok and errors:
            flash("Parametres sauvegardes avec erreurs. Watchtower redemarre.", "success")
        return redirect(url_for("settings"))
    current = load_settings()
    return render_template("settings.html", settings=current)

@app.route("/reset_metrics", methods=["POST"])
@login_required
def reset_metrics_route():
    reset_metrics()
    flash("Metriques reinitialisees.", "success")
    return redirect(url_for("settings"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
