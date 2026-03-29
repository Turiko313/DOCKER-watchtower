import os
import json
import functools

from flask import Flask, render_template, request, redirect, url_for, session, flash
import docker
import requests as http_requests

# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "admin")
WATCHTOWER_API_TOKEN = os.environ.get("WATCHTOWER_API_TOKEN", "")
WATCHTOWER_API_URL = os.environ.get("WATCHTOWER_API_URL", "http://watchtower:8080")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "watchtower.json")

# ---------------------------------------------------------------------------
# Docker client (socket mounted from host)
# ---------------------------------------------------------------------------
docker_client = docker.from_env()


# ===========================================================================
# Auth helpers
# ===========================================================================
def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
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
            return redirect(url_for("dashboard"))
        flash("Identifiants incorrects.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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
            timeout=10,
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
            image_name = c.image.tags[0] if c.image.tags else c.image.short_id
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
                "created": c.attrs["Created"][:19].replace("T", " "),
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
    }
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w") as fh:
        json.dump(settings, fh, indent=2)


def _settings_to_env(settings):
    env = {}
    if settings.get("schedule"):
        env["WATCHTOWER_SCHEDULE"] = settings["schedule"]
    else:
        env["WATCHTOWER_POLL_INTERVAL"] = settings.get("poll_interval", "86400")
    bool_map = {
        "cleanup": "WATCHTOWER_CLEANUP",
        "include_stopped": "WATCHTOWER_INCLUDE_STOPPED",
        "revive_stopped": "WATCHTOWER_REVIVE_STOPPED",
        "monitor_only": "WATCHTOWER_MONITOR_ONLY",
        "label_enable": "WATCHTOWER_LABEL_ENABLE",
        "rolling_restart": "WATCHTOWER_ROLLING_RESTART",
        "no_startup_message": "WATCHTOWER_NO_STARTUP_MESSAGE",
    }
    for key, env_var in bool_map.items():
        if settings.get(key):
            env[env_var] = "true"
    if settings.get("log_level"):
        env["WATCHTOWER_LOG_LEVEL"] = settings["log_level"]
    if settings.get("timeout"):
        env["WATCHTOWER_TIMEOUT"] = settings["timeout"]
    return env


# ===========================================================================
# Watchtower container recreation
# ===========================================================================
def _restart_watchtower():
    try:
        wt = docker_client.containers.get("watchtower")
        attrs = wt.attrs

        image = attrs["Config"]["Image"]
        labels = attrs["Config"]["Labels"] or {}
        binds = attrs["HostConfig"].get("Binds") or []
        port_bindings = attrs["HostConfig"].get("PortBindings") or {}
        restart_pol = attrs["HostConfig"].get("RestartPolicy") or {"Name": "unless-stopped"}
        networks = list((attrs["NetworkSettings"].get("Networks") or {}).keys())

        # Build new environment: keep base vars, merge dashboard settings
        base_env = {
            "TZ": os.environ.get("TZ", "Europe/Paris"),
            "WATCHTOWER_HTTP_API_METRICS": "true",
            "WATCHTOWER_HTTP_API_UPDATE": "true",
            "WATCHTOWER_HTTP_API_TOKEN": WATCHTOWER_API_TOKEN,
        }
        base_env.update(_settings_to_env(_load_settings()))
        env_list = [f"{k}={v}" for k, v in base_env.items()]

        # Convert port bindings to docker-py format
        ports = {}
        host_ports = {}
        for container_port, host_list in port_bindings.items():
            ports[container_port] = None
            if host_list:
                hp = host_list[0]
                host_ports[container_port] = (hp.get("HostIp", ""), int(hp["HostPort"]))

        # Stop and remove old container
        wt.stop(timeout=10)
        wt.remove()

        # Recreate
        new_wt = docker_client.containers.run(
            image=image,
            name="watchtower",
            detach=True,
            environment=env_list,
            volumes=binds,
            ports=host_ports,
            labels=labels,
            restart_policy=restart_pol,
        )

        # Reconnect to compose network
        for net_name in networks:
            if net_name != "bridge":
                try:
                    network = docker_client.networks.get(net_name)
                    network.connect(new_wt)
                except Exception:
                    pass

        return True
    except Exception as exc:
        flash(f"Erreur lors du redemarrage de Watchtower : {exc}", "error")
        return False


# ===========================================================================
# Entry point
# ===========================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
