import base64
import hashlib
import hmac
import os
import re
import secrets

from flask import Flask, Response, flash, jsonify, redirect, render_template, request, url_for
import requests as http_requests

from docker_helpers import container_states, get_update_statuses, list_containers, restart_containers
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
if len(DASHBOARD_PASSWORD) < 8:
    raise RuntimeError("DASHBOARD_PASSWORD doit contenir au moins 8 caracteres")

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


def _resolve_action_targets(values):
    unexpected = set(values) - {"csrf_token", "container", "image"}
    selectors = [key for key in ("container", "image") if key in values]
    if unexpected or len(selectors) != 1:
        return None, (jsonify({"status": "error", "message": "Cible d'action invalide."}), 400)
    selector = selectors[0]
    targets = values.getlist(selector)
    if len(targets) != 1 or not targets[0].strip():
        return None, (jsonify({"status": "error", "message": "Cible d'action vide ou invalide."}), 400)
    field = "name" if selector == "container" else "image"
    containers = [c for c in list_containers() if c[field] == targets[0]]
    if not containers:
        return None, (jsonify({"status": "error", "message": "Conteneur ou groupe introuvable. Actualisez la page."}), 404)
    return containers, None


@app.route("/update", methods=["POST"])
def trigger_update():
    params = {}
    # Never forward user-supplied patterns: Watchtower treats names as regexes.
    if request.data and not request.form:
        return jsonify({"status": "error", "message": "Format de requête invalide."}), 400
    targeted = bool(set(request.form) - {"csrf_token"})
    if targeted:
        targets, error = _resolve_action_targets(request.form)
        if error:
            return error
        names = [c["name"] for c in targets if c["watchtower_enabled"]]
        if not names:
            return jsonify({"status": "error", "message": "Watchtower est désactivé pour cette cible."}), 409
        params["container"] = ",".join("^" + re.escape(name) + "$" for name in names)
    try:
        response = http_requests.post(
            f"{WATCHTOWER_API_URL}/v1/update",
            headers={"Authorization": f"Bearer {WATCHTOWER_API_TOKEN}"},
            params=params,
            timeout=120,
        )
        if response.status_code == 200:
            try:
                summary = response.json()["summary"]
                counts = [summary[key] for key in ("failed", "updated", "scanned", "skipped")]
                if any(type(count) is not int or count < 0 for count in counts):
                    raise ValueError("Invalid counters")
                failed, updated, scanned, skipped = counts
                scan_failed = summary.get("scan_failed", False)
                if type(scan_failed) is not bool:
                    raise ValueError("Invalid scan status")
            except (KeyError, ValueError, TypeError, AttributeError):
                return jsonify({"status": "error", "message": "Réponse Watchtower invalide."}), 502
            message = (
                f"Vérification terminée : {scanned} vérifié(s), {updated} mis à jour, "
                f"{skipped} ignoré(s), {failed} échec(s)."
            )
            if failed or scan_failed:
                return jsonify({"status": "error", "message": message + " Consultez les journaux Watchtower."}), 502
            if targeted and scanned == 0:
                return jsonify({"status": "error", "message": message + " Aucun conteneur vérifié. Vérifiez les labels et l'inclusion des conteneurs arrêtés dans Settings."}), 409
            return jsonify(
                {"status": "success", "message": message}
            )
        app.logger.warning("Watchtower update API returned HTTP %s", response.status_code)
        return jsonify(
            {
                "status": "error",
                "message": f"Watchtower a repondu avec le code {response.status_code}.",
            }
        ), 502
    except http_requests.Timeout:
        return jsonify({"status": "error", "message": "Délai de réponse dépassé. La mise à jour peut encore être en cours : consultez les journaux Watchtower avant de relancer."}), 504
    except http_requests.RequestException:
        app.logger.exception("Unable to reach the Watchtower update API")
        return jsonify(
            {
                "status": "error",
                "message": "Impossible de contacter le service Watchtower.",
            }
        ), 502


@app.route("/restart/status", methods=["GET"])
def restart_status():
    targets, error = _resolve_action_targets(request.args)
    if error:
        return error
    try:
        return jsonify({"status": "success", "containers": container_states([c["name"] for c in targets])})
    except Exception:
        app.logger.exception("Unable to read restart state")
        return jsonify({"status": "error", "message": "Impossible de vérifier l'état des conteneurs."}), 502


@app.route("/restart", methods=["POST"])
def trigger_restart():
    if request.data and not request.form:
        return jsonify({"status": "error", "message": "Format de requête invalide."}), 400
    targets, error = _resolve_action_targets(request.form)
    if error:
        return error
    try:
        # Restart the dashboard last so other group members finish before the
        # connection closes. The browser verifies StartedAt on reconnection.
        states = container_states([c["name"] for c in targets])
        own_id = os.environ.get("HOSTNAME", "")
        states.sort(key=lambda c: bool(own_id and c["id"].startswith(own_id)))
        results = restart_containers([c["name"] for c in states])
    except Exception:
        app.logger.exception("Unable to contact Docker for restart")
        return jsonify({"status": "error", "message": "Impossible de contacter Docker pour le redémarrage."}), 502
    failed = [r for r in results if r["status"] != "success"]
    status = "error" if failed else "success"
    message = f"Redémarrage : {len(results) - len(failed)} réussi(s), {len(failed)} échec(s)."
    if failed:
        message += " " + " ; ".join(r["name"] + " : " + r["message"] for r in failed)
    return jsonify({"status": status, "message": message, "results": results}), (502 if failed else 200)


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
