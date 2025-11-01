# Runbook — Blue/Green Observability & Alerts

This runbook explains the alerts produced by the watcher and suggested operator actions.

## Alert types

### 1) Failover Detected
**What it means:** The watcher observed a change in `X-App-Pool` in Nginx access logs (e.g., `blue → green`), indicating Nginx routed traffic to the backup pool.  
**Likely causes:** Blue experienced timeouts or 5xx errors and was marked failed.  
**Operator action:**
1. Check Nginx logs:
2. Check Blue container health and logs:
3. If Blue is unhealthy, inspect `/chaos` endpoints (if testing), resource usage, and application logs.
4. After remediation, either allow auto-recovery or manually toggle `ACTIVE_POOL` back if required.
   <img width="785" height="213" alt="image" src="https://github.com/user-attachments/assets/65bd94bb-746e-4426-a89d-7671d49dc3ae" />



### 2) High Error Rate
**What it means:** The watcher detected that upstream 5xxs exceeded the configured threshold (ERROR_RATE_THRESHOLD) over the last WINDOW_SIZE requests.  
**Likely causes:** Gradual degradation in primary pool, third-party dependency failure, or traffic spike causing backend errors.  
**Operator action:**
1. Inspect recent Nginx lines:
2. Identify which upstream (app_blue/app_green) has the high 5xxs.
3. Inspect the application's logs:
4. If necessary, toggle `ACTIVE_POOL` in `.env` and reload Nginx (planned maintenance).
5. If it's an infrastructure problem (resource exhaustion), scale or restart containers.
   <img width="796" height="219" alt="image" src="https://github.com/user-attachments/assets/d79915b7-399e-4958-af57-a032d03cbeff" />


### 3) Recovery Notices
When the primary pool is healthy again, the watcher will detect pool flips back and may post a recovery message. Confirm app behavior and mark the incident as resolved.

## Maintenance Mode
Set `MAINTENANCE_MODE=true` in `.env` to suppress alerts during planned toggles or maintenance windows.

## Suppressing Alerts Temporarily
- Set `MAINTENANCE_MODE=true`, then perform your maintenance.
- Revert `MAINTENANCE_MODE=false` afterward.

## Troubleshooting tips
- Verify watcher can read logs: `docker compose exec alert_watcher ls /var/log/nginx`
- Verify Slack webhook connectivity by running a manual POST inside the watcher container:
```bash
docker compose exec alert_watcher python -c "import requests; print(requests.post('$SLACK_WEBHOOK_URL', json={'text':'test'}).status_code)"

---

### 7) `README.md` (append / update sections)
Add to your existing `README.md` sections for observability and verification.

Suggested snippet:

```markdown
## Observability & Alerts (Stage 3)

This project includes a lightweight Python `alert_watcher` that tails Nginx access logs and posts alerts to Slack when:
- a failover occurs (Blue → Green or Green → Blue)
- the upstream 5xx error rate exceeds a configured threshold

### How to run
1. Ensure `.env` exists and contains `SLACK_WEBHOOK_URL` (set on server only).
2. Start the stack:
   ```bash
   docker compose up -d
3. Confirm watcher is running:
   docker compose ps
   docker compose logs -f alert_watcher
