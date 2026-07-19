import os
import subprocess
import time
import json
import tempfile
import requests as http_requests
from flask import flash

WATCHTOWER_API_TOKEN = os.environ.get("WATCHTOWER_HTTP_API_TOKEN", "")
WATCHTOWER_API_URL = os.environ.get("WATCHTOWER_API_URL", "http://localhost:8080")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
METRICS_FILE = os.path.join(CONFIG_DIR, "metrics_history.json")

METRICS_TO_TRACK = (
    "watchtower_containers_updated",
    "watchtower_scans_total",
    "watchtower_scans_skipped",
    "watchtower_scans_failed",
)

def _load_metrics():
    try:
        with open(METRICS_FILE, "r", encoding="utf-8") as f:
            metrics = json.load(f)
        return metrics if isinstance(metrics, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

def _save_metrics(data):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    fd, temporary_file = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".metrics-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_file, METRICS_FILE)
    except OSError:
        try:
            os.unlink(temporary_file)
        except FileNotFoundError:
            pass
        raise

def _fetch_watchtower_metrics():
    """Return metrics, or ``None`` when Watchtower cannot be reached."""
    try:
        resp = http_requests.get(
            f"{WATCHTOWER_API_URL}/v1/metrics",
            headers={"Authorization": f"Bearer {WATCHTOWER_API_TOKEN}"},
            timeout=5,
        )
        if resp.status_code == 200:
            return parse_prometheus(resp.text)
    except http_requests.RequestException:
        pass
    return None


def _counter(value):
    """Return a safe integer counter value from persisted or Prometheus data."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def reset_metrics():
    """Reset local totals while retaining the current Watchtower counter baseline."""
    current_metrics = _fetch_watchtower_metrics()
    previous_metrics = _load_metrics()
    baseline = {}
    for key in METRICS_TO_TRACK:
        previous = previous_metrics.get(key, {})
        previous_last_seen = previous.get("last_seen", 0) if isinstance(previous, dict) else 0
        baseline[key] = {
            "cumulative": 0,
            "last_seen": _counter(
                current_metrics.get(key, previous_last_seen)
                if current_metrics is not None
                else previous_last_seen
            ),
        }
    _save_metrics(baseline)

def parse_prometheus(text):
    metrics = {}
    for line in text.strip().splitlines():
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            try:
                metrics[parts[0]] = int(float(parts[1]))
            except ValueError:
                pass
    return metrics

def get_watchtower_metrics():
    current_metrics = _fetch_watchtower_metrics()
    stored = _load_metrics()
    result = {}
    changed = False

    for key in METRICS_TO_TRACK:
        state = stored.get(key, {})
        state = state if isinstance(state, dict) else {}
        cumulative = _counter(state.get("cumulative"))
        last_val = _counter(state.get("last_seen"))
        if current_metrics is None:
            result[key] = cumulative
            continue

        cur_val = _counter(current_metrics.get(key))

        if cur_val >= last_val:
            cumulative += cur_val - last_val
        else:
            # Watchtower restarted, counter reset
            cumulative += cur_val

        updated_state = {"cumulative": cumulative, "last_seen": cur_val}
        if state != updated_state:
            changed = True
        stored[key] = updated_state
        result[key] = cumulative

    if changed:
        _save_metrics(stored)

    return result

def restart_watchtower():
    try:
        result = subprocess.run(
            ["supervisorctl", "restart", "watchtower"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
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
                flash(f"Watchtower redemarre mais statut inattendu : {check.stdout.strip()}", "error")
                return False
        else:
            flash(f"Erreur supervisorctl : {result.stderr}", "error")
            return False
    except Exception as exc:
        flash(f"Erreur lors du redemarrage de Watchtower : {exc}", "error")
        return False

