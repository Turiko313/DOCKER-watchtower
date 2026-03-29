#!/bin/sh
# Read dashboard settings from JSON and export as env vars before starting watchtower

SETTINGS_FILE="/config/watchtower.json"

if [ -f "$SETTINGS_FILE" ]; then
    eval $(python3 -c "
import json
try:
    with open('$SETTINGS_FILE') as f:
        s = json.load(f)
    if s.get('schedule'):
        print(f'export WATCHTOWER_SCHEDULE=\"{s[\"schedule\"]}\"')
    else:
        print(f'export WATCHTOWER_POLL_INTERVAL=\"{s.get(\"poll_interval\", \"86400\")}\"')
    bools = {
        'cleanup': 'WATCHTOWER_CLEANUP',
        'include_stopped': 'WATCHTOWER_INCLUDE_STOPPED',
        'revive_stopped': 'WATCHTOWER_REVIVE_STOPPED',
        'monitor_only': 'WATCHTOWER_MONITOR_ONLY',
        'label_enable': 'WATCHTOWER_LABEL_ENABLE',
        'rolling_restart': 'WATCHTOWER_ROLLING_RESTART',
        'no_startup_message': 'WATCHTOWER_NO_STARTUP_MESSAGE',
    }
    for k, v in bools.items():
        if s.get(k):
            print(f'export {v}=\"true\"')
    if s.get('log_level'):
        print(f'export WATCHTOWER_LOG_LEVEL=\"{s[\"log_level\"]}\"')
    if s.get('timeout'):
        print(f'export WATCHTOWER_TIMEOUT=\"{s[\"timeout\"]}\"')
except:
    pass
")
fi

exec /usr/local/bin/watchtower
