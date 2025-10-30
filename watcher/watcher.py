#!/usr/bin/env python3
"""
Log watcher for Blue/Green task.
Tails /var/log/nginx/access.log, parses structured lines produced by nginx,
detects pool flips and elevated 5xx error rates, and posts alerts to Slack.
"""

import os
import re
import time
import requests
from collections import deque
from datetime import datetime, timedelta

# --- Configuration via environment (defaults provided) ---
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

# --- Alerting Functions ---

def post_slack(text, attachments=None):
    """Posts a message to Slack or prints to stdout if no webhook is configured."""
    payload = {"text": text}
    if attachments:
        payload["attachments"] = attachments
    
    now_utc = datetime.utcnow().isoformat()
    if SLACK_WEBHOOK:
        try:
            requests.post(SLACK_WEBHOOK, json=payload, timeout=5)
        except Exception as e:
            print(f"[{now_utc}] Failed to post to Slack: {e}")
    else:
        # Use a distinguishable format for stdout alerts
        print(f"[{now_utc}] [*** ALERT ***] {text.replace('\\n', ' | ')}")

def handle_line(line):
    """Processes a single log line, detecting pool flips and error rate breaches."""
    global last_seen_pool, last_failover_alert_ts, last_error_rate_alert_ts

    m = LOG_RE.match(line)
    if not m:
        return

    status = int(m.group("status"))
    pool = m.group("pool")
    release = m.group("release")
    upstream_status = m.group("upstream_status")
    
    # --- 1. Detect Pool Flip ---
    now = datetime.utcnow()
    if last_seen_pool is None:
        last_seen_pool = pool
    elif pool != last_seen_pool:
        # Failover detected
        if MAINTENANCE_MODE:
            print(f"[{now.isoformat()}] Maintenance mode ON: suppressing failover alert from {last_seen_pool}→{pool}")
        else:
            cooldown_time_elapsed = (now - last_failover_alert_ts).total_seconds() >= ALERT_COOLDOWN_SEC if last_failover_alert_ts else True
            
            if cooldown_time_elapsed:
                text = (
                    f":rotating_light: Failover detected: *{last_seen_pool}* \u2192 *{pool}*\n" # Using unicode arrow
                    f"Release: {release}\n"
                    f"Time: {now.isoformat()}Z"
                )
                post_slack(text)
                last_failover_alert_ts = now
            else:
                print(f"[{now.isoformat()}] Failover detected but in cooldown, suppressed.")
        
        last_seen_pool = pool

    # --- 2. Track errors for error rate ---
    is_error = False
    try:
        # Treat 5xx from upstream_status or main status as error
        us = int(upstream_status) if upstream_status.isdigit() and upstream_status != '-' else 0
    except ValueError:
        us = 0
        
    if 500 <= us < 600 or 500 <= status < 600:
        is_error = True

    rolling.append(1 if is_error else 0)

    # Only evaluate once the window is reasonably full
    if len(rolling) >= 10: 
        error_count = sum(rolling)
        total = len(rolling)
        error_rate = (error_count / total) * 100.0

        if error_rate >= ERROR_RATE_THRESHOLD:
            cooldown_time_elapsed = (now - last_error_rate_alert_ts).total_seconds() >= ALERT_COOLDOWN_SEC if last_error_rate_alert_ts else True

            if MAINTENANCE_MODE:
                print(f"[{now.isoformat()}] Maintenance mode ON: suppressing error-rate alert ({error_rate:.2f}%)")
            elif cooldown_time_elapsed:
                text = (
                    f":warning: High upstream error rate detected: *{error_rate:.2f}%* "
                    f"({error_count}/{total}) over last {total} requests.\n"
                    f"Threshold: {ERROR_RATE_THRESHOLD}%\n"
                    f"Time: {now.isoformat()}Z"
                )
                post_slack(text)
                last_error_rate_alert_ts = now
            else:
                print(f"[{now.isoformat()}] Error rate {error_rate:.2f}% breached but in cooldown.")

# --- Tailing Logic with Rotation Handling ---

def follow(filepath, delay=0.1):
    """
    Tails a file forever, yielding new lines as they come in.
    Handles file rotation by checking inode and file size.
    """
    
    # We open the file here instead of main() to support re-opening on rotation
    while True:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as file:
                # Go to EOF on first run or on re-open
                file.seek(0, os.SEEK_END)
                print(f"[{datetime.utcnow().isoformat()}] File opened. Starting tail from position: {file.tell()}")
                
                # Get initial inode for rotation check
                initial_inode = os.fstat(file.fileno()).st_ino

                while True:
                    # Check for rotation: if inode or size has changed unexpectedly
                    try:
                        current_stat = os.fstat(file.fileno())
                        if current_stat.st_ino != initial_inode:
                            print(f"[{datetime.utcnow().isoformat()}] Log file rotated (inode changed). Re-opening.")
                            break # Exit inner loop, triggers file re-open
                        
                        # Handle truncation (file smaller than current position)
                        current_pos = file.tell()
                        if current_stat.st_size < current_pos:
                            print(f"[{datetime.utcnow().isoformat()}] Log file truncated/rotated (size decreased). Seeking to start.")
                            file.seek(0)
                            
                    except FileNotFoundError:
                        print(f"[{datetime.utcnow().isoformat()}] Log file disappeared. Waiting for it to reappear...")
                        break # Exit inner loop, triggers file re-open
                    except Exception as e:
                        print(f"[{datetime.utcnow().isoformat()}] Error during file stat check: {e}")
                        time.sleep(1) # Wait a bit before retry
                        
                    # Read lines
                    line = file.readline()
                    if not line:
                        time.sleep(delay)
                        continue
                        
                    yield line

        except FileNotFoundError:
            print(f"[{datetime.utcnow().isoformat()}] Waiting for log file {filepath}...")
            time.sleep(5) # Wait longer for file to appear
        except Exception as e:
            print(f"[{datetime.utcnow().isoformat()}] Unexpected error in follow loop: {e}")
            time.sleep(5)


# --- Main Execution ---

def main():
    """Main execution point."""
    print(f"Watcher starting. Configuration:")
    print(f"- Log Path: {LOG_PATH}")
    print(f"- Error Threshold: {ERROR_RATE_THRESHOLD}% / {WINDOW_SIZE} reqs")
    print(f"- Alert Cooldown: {ALERT_COOLDOWN_SEC}s")
    print(f"- Maintenance Mode: {'ON' if MAINTENANCE_MODE else 'OFF'}")

    # The file opening is now handled inside follow() to manage rotation
    for line in follow(LOG_PATH):
        try:
            # Added a strip() just in case the line includes excessive whitespace
            handle_line(line.strip())
        except Exception as e:
            print(f"[{datetime.utcnow().isoformat()}] Error handling line: {e}; line: {line.strip()}")

if __name__ == "__main__":
    main()