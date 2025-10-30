#!/usr/bin/env python3
"""
Log watcher for Blue/Green task.
Tails /var/log/nginx/access.log, parses structured lines produced by nginx,
detects pool flips and elevated 5xx error rates, and posts alerts to Slack.
"""

import os
import re
import time
import json
import requests
from collections import deque
from datetime import datetime, timedelta

# Configuration via environment (defaults provided)
LOG_PATH = os.environ.get("NGINX_LOG_PATH", "/var/log/nginx/access.log")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
ERROR_RATE_THRESHOLD = float(os.environ.get("ERROR_RATE_THRESHOLD", "2.0"))  # percent
WINDOW_SIZE = int(os.environ.get("WINDOW_SIZE", "200"))
ALERT_COOLDOWN_SEC = int(os.environ.get("ALERT_COOLDOWN_SEC", "300"))
MAINTENANCE_MODE = os.environ.get("MAINTENANCE_MODE", "false").lower() in ("1", "true", "yes")

if not SLACK_WEBHOOK:
    print("Warning: SLACK_WEBHOOK_URL not set. Alerts will be printed to stdout only.")

# Regex to parse the structured log format
# Example: ... status=200 pool=blue release=v1.0 upstream_status=200 upstream_addr=172.19.0.3:8000 request_time=0.003 upstream_response_time=0.002 ...
LOG_RE = re.compile(
    r'.*status=(?P<status>\d+)\s+pool=(?P<pool>\S+)\s+release=(?P<release>\S+)\s+'
    r'upstream_status=(?P<upstream_status>\S+)\s+upstream_addr=(?P<upstream_addr>\S+)\s+'
    r'request_time=(?P<request_time>[\d\.]+)\s+upstream_response_time=(?P<upstream_response_time>[\d\.]+)'
)

# Rolling window of last WINDOW_SIZE booleans (True if 5xx)
rolling = deque(maxlen=WINDOW_SIZE)

# Track last seen pool to detect flips
last_seen_pool = None

# Track cooldown timestamps for alerts
last_failover_alert_ts = None
last_error_rate_alert_ts = None

def post_slack(text, attachments=None):
    payload = {"text": text}
    if attachments:
        payload["attachments"] = attachments
    if SLACK_WEBHOOK:
        try:
            requests.post(SLACK_WEBHOOK, json=payload, timeout=5)
        except Exception as e:
            print(f"[{datetime.utcnow().isoformat()}] Failed to post to Slack: {e}")
    else:
        print(f"[ALERT] {text}")

def handle_line(line):
    global last_seen_pool, last_failover_alert_ts, last_error_rate_alert_ts

    m = LOG_RE.match(line)
    if not m:
        return

    status = int(m.group("status"))
    pool = m.group("pool")
    release = m.group("release")
    upstream_status = m.group("upstream_status")
    upstream_addr = m.group("upstream_addr")
    request_time = m.group("request_time")
    upstream_response_time = m.group("upstream_response_time")

    # Detect pool flip
    if last_seen_pool is None:
        last_seen_pool = pool
    elif pool != last_seen_pool:
        # failover detected
        now = datetime.utcnow()
        if MAINTENANCE_MODE:
            print(f"[{now.isoformat()}] Maintenance mode ON: suppressing failover alert from {last_seen_pool}→{pool}")
        else:
            if not last_failover_alert_ts or (now - last_failover_alert_ts).total_seconds() >= ALERT_COOLDOWN_SEC:
                text = f":rotating_light: Failover detected: *{last_seen_pool}* → *{pool}*\nRelease: {release}\nUpstream: {upstream_addr}\nTime: {datetime.utcnow().isoformat()}Z"
                post_slack(text)
                last_failover_alert_ts = now
            else:
                print(f"[{now.isoformat()}] Failover detected but in cooldown, suppressed.")
        last_seen_pool = pool

    # Track errors for error rate (consider upstream_status or status)
    is_error = False
    # treat 5xx from upstream_status or main status as error
    try:
        us = int(upstream_status) if upstream_status.isdigit() else 0
    except:
        us = 0
    if 500 <= us < 600 or 500 <= status < 600:
        is_error = True

    rolling.append(1 if is_error else 0)

    # Only evaluate once we have at least some entries (e.g., avoid division by zero)
    if len(rolling) >= 10:  # small warming buffer
        error_count = sum(rolling)
        total = len(rolling)
        error_rate = (error_count / total) * 100.0

        if error_rate >= ERROR_RATE_THRESHOLD:
            now = datetime.utcnow()
            if MAINTENANCE_MODE:
                print(f"[{now.isoformat()}] Maintenance mode ON: suppressing error-rate alert ({error_rate:.2f}%)")
            else:
                if not last_error_rate_alert_ts or (now - last_error_rate_alert_ts).total_seconds() >= ALERT_COOLDOWN_SEC:
                    text = f":warning: High upstream error rate detected: *{error_rate:.2f}%* ({error_count}/{total}) over last {total} requests.\nThreshold: {ERROR_RATE_THRESHOLD}%\nTime: {datetime.utcnow().isoformat()}Z"
                    post_slack(text)
                    last_error_rate_alert_ts = now
                else:
                    print(f"[{now.isoformat()}] Error rate {error_rate:.2f}% breached but in cooldown.")
        # else: nothing to do

def follow(file):
    """Tail a file forever, yielding new lines as they come in"""
    file.seek(0,2)  # go to EOF
    while True:
        line = file.readline()
        if not line:
            time.sleep(0.1)
            continue
        yield line

def main():
    print(f"Watcher starting. Watching {LOG_PATH}")
    # Ensure file exists
    while not os.path.exists(LOG_PATH):
        print(f"Waiting for log file {LOG_PATH}...")
        time.sleep(1)

    with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
        for line in follow(f):
            try:
                handle_line(line)
            except Exception as e:
                print(f"Error handling line: {e}; line: {line.strip()}")

if __name__ == "__main__":
    main()
