import os
import subprocess
import time
import json
import requests as http_requests
from flask import flash

WATCHTOWER_API_TOKEN = os.environ.get("WATCHTOWER_HTTP_API_TOKEN", "")
WATCHTOWER_API_URL = os.environ.get("WATCHTOWER_API_URL", "http://localhost:8080")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
METRICS_FILE = os.path.join(CONFIG_DIR, "metrics_history.json")

def _load_metrics():
    try:
        with open(METRICS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_metrics(data):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(METRICS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def reset_metrics():
    _save_metrics({})

def parse_prometheus(text):
    metrics = {}
    for line in text.strip().splitlines():
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            try:
                metrics[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return metrics

def get_watchtower_metrics():
    current_metrics = {}
    try:
        resp = http_requests.get(
            f"{WATCHTOWER_API_URL}/v1/metrics",
            headers={"Authorization": f"Bearer {WATCHTOWER_API_TOKEN}"},
            timeout=5,
        )
        if resp.status_code == 200:
            current_metrics = parse_prometheus(resp.text)
    except Exception:
        pass
    
    stored = _load_metrics()
    
    keys_to_track = [
        "watchtower_containers_updated",
        "watchtower_scans_total",
        "watchtower_scans_skipped",
        "watchtower_scans_failed"
    ]
    
    result = {}
    changed = False
    
    for key in keys_to_track:
        if key not in stored:
            stored[key] = {"cumulative": 0, "last_seen": 0}
            
        cur_val = current_metrics.get(key, 0)
        last_val = stored[key]["last_seen"]
        
        if cur_val >= last_val:
            stored[key]["cumulative"] += (cur_val - last_val)
        else:
            # Watchtower restarted, counter reset
            stored[key]["cumulative"] += cur_val
            
        stored[key]["last_seen"] = cur_val
        result[key] = stored[key]["cumulative"]
        
        if cur_val != last_val:
            changed = True
            
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

