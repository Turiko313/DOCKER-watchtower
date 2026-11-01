import os
import subprocess
import time
import json
import tempfile
import threading
from contextlib import contextmanager
import requests as http_requests
from flask import flash


def _load_api_token():
    value = os.environ.get("WATCHTOWER_HTTP_API_TOKEN", "").strip()
    if value and os.path.isfile(value):
        try:
            with open(value, "r", encoding="utf-8") as token_file:
                value = token_file.read().strip()
        except OSError as exc:
            raise RuntimeError("Impossible de lire WATCHTOWER_HTTP_API_TOKEN") from exc
    if not value:
        raise RuntimeError("WATCHTOWER_HTTP_API_TOKEN est obligatoire")
    if len(value) < 32:
        raise RuntimeError(
            "WATCHTOWER_HTTP_API_TOKEN doit contenir au moins 32 caracteres"
        )
    return value


WATCHTOWER_API_TOKEN = _load_api_token()
WATCHTOWER_API_URL = os.environ.get("WATCHTOWER_API_URL", "http://localhost:8080")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
METRICS_FILE = os.path.join(CONFIG_DIR, "metrics_history.json")

COUNTERS_TO_TRACK = (
    "watchtower_scans_total",
    "watchtower_scans_skipped_total",
)
GAUGES_TO_TRACK = (
    "watchtower_containers_updated",
    "watchtower_containers_failed",
)
_metrics_thread_lock = threading.Lock()


@contextmanager
def _metrics_transaction():
    """Serialize read/modify/write across threads and Linux Gunicorn workers."""
    with _metrics_thread_lock:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(os.path.join(CONFIG_DIR, ".metrics.lock"), "a+b") as lock_file:
            if os.name == "posix":
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "posix":
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def get_watched_containers():
    """Read the running engine's selection and effective per-container modes.

    None means unavailable; an empty mapping means no containers are watched.
    Never infer selection from saved settings, which may not have been applied.
    """
    try:
        response = http_requests.get(
            f"{WATCHTOWER_API_URL}/v1/containers/details",
            headers={"Authorization": f"Bearer {WATCHTOWER_API_TOKEN}"},
            timeout=5,
        )
        response.raise_for_status()
        entries = response.json()["containers"]
        if not isinstance(entries, list):
            return None
        watched = {}
        for entry in entries:
            if (not isinstance(entry, dict)
                    or not isinstance(entry.get("name"), str)
                    or not entry["name"].lstrip("/")
                    or any(type(entry.get(key)) is not bool
                           for key in ("enabled", "monitor_only", "no_pull"))):
                return None
            name = entry["name"].lstrip("/")
            if name in watched:
                return None
            watched[name] = {key: entry[key] for key in ("enabled", "monitor_only", "no_pull")}
        return watched
    except (http_requests.RequestException, ValueError, KeyError, TypeError):
        return None

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
    except (TypeError, ValueError, OverflowError):
        return 0


def reset_metrics():
    """Reset local totals while retaining the current Watchtower counter baseline."""
    with _metrics_transaction():
        _reset_metrics()


def _reset_metrics():
    current_metrics = _fetch_watchtower_metrics()
    previous_metrics = _load_metrics()
    baseline = {}
    for key in COUNTERS_TO_TRACK:
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
    # These are gauges describing the latest scan, not increasing counters.
    # Keep their true values even when local cumulative counters are reset.
    for key in GAUGES_TO_TRACK:
        previous = previous_metrics.get(key, {})
        last_value = (previous.get("last_value", previous.get("last_seen", 0))
                      if isinstance(previous, dict) else 0)
        baseline[key] = {"last_value": _counter(
            current_metrics.get(key, last_value)
            if current_metrics is not None else last_value
        )}
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
            except (ValueError, OverflowError):
                pass
    return metrics

def get_watchtower_metrics():
    with _metrics_transaction():
        return _get_watchtower_metrics()


def _get_watchtower_metrics():
    current_metrics = _fetch_watchtower_metrics()
    stored = _load_metrics()
    result = {}
    changed = False

    for key in COUNTERS_TO_TRACK:
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

    for key in GAUGES_TO_TRACK:
        state = stored.get(key, {})
        state = state if isinstance(state, dict) else {}
        value = _counter(state.get("last_value", state.get("last_seen", 0)))
        if current_metrics is not None:
            value = _counter(current_metrics.get(key))
        updated_state = {"last_value": value}
        if state != updated_state:
            changed = True
        stored[key] = updated_state
        result[key] = value

    if changed:
        _save_metrics(stored)

    return result

def restart_watchtower():
    try:
        result = subprocess.run(
            [
                "supervisorctl",
                "-c",
                "/etc/supervisor/conf.d/supervisord.conf",
                "restart",
                "watchtower",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            time.sleep(2)
            check = subprocess.run(
                [
                    "supervisorctl",
                    "-c",
                    "/etc/supervisor/conf.d/supervisord.conf",
                    "status",
                    "watchtower",
                ],
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

