#!/usr/bin/env python3
"""
watcher.py
Tails /var/log/nginx/access.log (structured log lines) and sends Slack alerts for:
 - Failover events (pool change e.g. blue -> green)
 - Elevated 5xx error rate over a sliding window
Uses env vars from .env (via docker-compose env_file).
"""

import os
import time
import re
import json
import collections
from datetime import datetime, timedelta

import requests

LOG_PATH = os.environ.get("NGINX_LOG_PATH", "/var/log/nginx/structured_access.log")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
ACTIVE_POOL = os.environ.get("ACTIVE_POOL", "blue").lower()
ERROR_RATE_THRESHOLD = float(os.environ.get("ERROR_RATE_THRESHOLD", "2.0"))  # percent
WINDOW_SIZE = int(os.environ.get("WINDOW_SIZE", "200"))
ALERT_COOLDOWN_SEC = int(os.environ.get("ALERT_COOLDOWN_SEC", "300"))
MAINTENANCE_MODE = os.environ.get("MAINTENANCE_MODE", "false").lower() in ("1", "true", "yes")

if not SLACK_WEBHOOK_URL:
    print("ERROR: SLACK_WEBHOOK_URL not set. Exiting.")
    raise SystemExit(1)

# pattern to extract key fields from structured nginx log lines
# We logged: pool="...|..." release="...|..." upstream_status="..." upstream_addr="..." request_time="..."
LOG_RE = re.compile(
    r'pool="(?P<pool>[^"]+)"\s+release="(?P<release>[^"]+)"\s+upstream_status="(?P<upstream_status>[^"]*)"'
    r'\s+upstream_addr="(?P<upstream_addr>[^"]*)"'
    r'\s+request_time="(?P<request_time>[^"]*)"'
)

# helper to post Slack message
def post_slack(text, attachments=None):
    payload = {"text": text}
    if attachments:
        payload["attachments"] = attachments
    try:
        r = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=5)
        if r.status_code >= 300:
            print(f"Slack post failed {r.status_code}: {r.text}")
    except Exception as e:
        print("Slack send error:", e)

# tailer generator that yields new lines
import time

def follow(filepath):
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        try:
            if f.seekable():
                f.seek(0, 2)  # Move to EOF only if seekable
        except (OSError, IOError):
            pass  # Ignore if stream is not seekable
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            yield line


def normalize_pool_field(raw):
    """raw is like 'upstream_val|fallback_val'. prefer the first non-empty before '|'."""
    parts = raw.split("|")
    for p in parts:
        p = p.strip()
        if p and p.lower() != "-":
            return p.lower()
    return None

def parse_upstream_status(raw):
    """upstream_status may be empty or '200' or '200, 502'. take the last meaningful code."""
    if not raw:
        return None
    # some logs may have comma-separated statuses; choose last numeric token
    tokens = re.findall(r'\d{3}', raw)
    return tokens[-1] if tokens else None

def now_ts():
    return datetime.utcnow().isoformat() + "Z"

def main():
    print(f"[{now_ts()}] watcher starting. Watching {LOG_PATH}")
    last_seen_pool = ACTIVE_POOL
    # rolling window of last WINDOW_SIZE booleans (True=5xx)
    window = collections.deque(maxlen=WINDOW_SIZE)
    total_seen = 0

    # cooldown trackers (dict of event_type -> datetime of last alert)
    last_alert = {"failover": None, "error_rate": None}

    # Ensure log exists before following
    while not os.path.exists(LOG_PATH):
        print(f"Waiting for log at {LOG_PATH} ...")
        time.sleep(0.5)

    for line in follow(LOG_PATH):
        m = LOG_RE.search(line)
        if not m:
            # ignore unparseable lines but keep scanning
            continue

        raw_pool = m.group("pool")
        pool = normalize_pool_field(raw_pool) or last_seen_pool
        upstream_status_raw = m.group("upstream_status")
        upstream_status = parse_upstream_status(upstream_status_raw)
        upstream_addr = m.group("upstream_addr")
        request_time = m.group("request_time")
        release_raw = m.group("release")
        release = normalize_pool_field(release_raw) or "unknown"

        total_seen += 1

        # determine if this request was an upstream 5xx
        is_5xx = False
        if upstream_status:
            try:
                code = int(upstream_status)
                is_5xx = 500 <= code < 600
            except ValueError:
                is_5xx = False

        window.append(1 if is_5xx else 0)

        # compute error rate if window filled or partial
        window_count = len(window)
        error_sum = sum(window)
        error_rate = (error_sum / window_count) * 100 if window_count > 0 else 0.0

        # FAILOVER detection: when pool changes compared to last_seen_pool
        if pool != last_seen_pool:
            # Only alert if not in maintenance mode
            if MAINTENANCE_MODE:
                print(f"[{now_ts()}] Pool change detected ({last_seen_pool} -> {pool}) but maintenance mode ON, suppressing.")
            else:
                # cooldown check
                last = last_alert.get("failover")
                if not last or (datetime.utcnow() - last).total_seconds() > ALERT_COOLDOWN_SEC:
                    text = f":rotating_light: *Failover detected* — {last_seen_pool} → {pool}\n" \
                           f"Upstream: {upstream_addr}  release: {release}  time: {request_time}\n" \
                           f"Sample log: `{line.strip()}`"
                    post_slack(text)
                    last_alert["failover"] = datetime.utcnow()
                    print(f"[{now_ts()}] Sent failover alert: {last_seen_pool} -> {pool}")
                else:
                    print(f"[{now_ts()}] Failover detected but in cooldown; suppressed.")

            last_seen_pool = pool

        # ERROR RATE detection: check threshold when window has at least some entries
        if window_count >= 10:  # start checking after a few requests to avoid noise
            if error_rate > ERROR_RATE_THRESHOLD:
                last = last_alert.get("error_rate")
                if not last or (datetime.utcnow() - last).total_seconds() > ALERT_COOLDOWN_SEC:
                    text = (f":warning: *High upstream error rate detected* — {error_rate:.2f}% 5xx "
                            f"over last {window_count} requests (threshold {ERROR_RATE_THRESHOLD}%).\n"
                            f"Current pool: {pool}  upstream: {upstream_addr}  release: {release}\n"
                            f"Most recent log: `{line.strip()}`")
                    post_slack(text)
                    last_alert["error_rate"] = datetime.utcnow()
                    print(f"[{now_ts()}] Sent error-rate alert: {error_rate:.2f}%")
                else:
                    print(f"[{now_ts()}] Error-rate high ({error_rate:.2f}%) but in cooldown; suppressed.")

        # small heartbeat to stdout every 100 requests
        if total_seen % 100 == 0:
            print(f"[{now_ts()}] processed {total_seen} log lines; window={window_count} error_rate={error_rate:.2f}%")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Watcher exiting (keyboard interrupt).")