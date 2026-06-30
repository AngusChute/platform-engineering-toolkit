# slack-alert-digest

A lightweight webhook receiver that aggregates noisy alerts into a single, readable Slack digest, instead of one Slack message per firing alert.

## Why this exists

Most alerting tools (Alertmanager, Grafana alerting, Dynatrace, etc.) can post directly to Slack — but during a real degraded period, that means a flood of near-duplicate messages: the same disk-usage alert firing every minute, twenty pods individually reporting `CrashLoopBackOff` because of one bad config push, and so on. That flood buries the signal that actually matters, makes on-call triage harder, and over time trains engineers to mute the channel — which is the opposite of what alerting is supposed to do.

This tool sits between your alert sources and Slack: it buffers incoming alerts, groups duplicates, and flushes a single severity-sorted digest on a fixed interval (5 minutes by default) instead of forwarding every alert individually.

## How it works

1. Alert sources (Alertmanager webhook receiver, a custom Grafana notification channel, etc.) POST to `/alert` with a small JSON payload: `source`, `severity`, `summary`
2. Alerts are buffered in memory, grouped by `(source, summary)` to collapse duplicates from flapping checks
3. Every `--interval` seconds, a digest is built — critical alerts first, then warnings, then info — and posted to Slack as a single message
4. A `/healthz` endpoint is included for liveness/readiness probes if you run this inside Kubernetes

## Example digest output

```
Alert Digest — 14 alert(s) in the last 5 min

🔴 CRITICAL (3)
  • [prometheus] Disk usage above 90% on node-3 (x3)

🟡 WARNING (10)
  • [grafana] Pod restart count elevated: api-deployment (x8)
  • [dynatrace] Response time degraded on /checkout (x2)

🔵 INFO (1)
  • [flux] Reconciliation completed for apps-production
```

## Usage

```bash
pip install -r requirements.txt

export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."

python alert_digest.py --interval 300 --port 8080 --slack-webhook-url "$SLACK_WEBHOOK_URL"
```

Send a test alert:

```bash
curl -X POST http://localhost:8080/alert \
  -H "Content-Type: application/json" \
  -d '{"source": "prometheus", "severity": "critical", "summary": "Disk usage above 90% on node-3"}'
```

### Wiring up Alertmanager

In `alertmanager.yml`:

```yaml
receivers:
  - name: alert-digest
    webhook_configs:
      - url: http://alert-digest-service:8080/alert
        send_resolved: false
```

(You'd add a small transform step or adjust the Flask route if Alertmanager's native webhook payload shape doesn't match the `source`/`severity`/`summary` fields this tool expects — Alertmanager's payload is more deeply nested. The current implementation favors simplicity and is easiest to front with a low-code transform, e.g. a Logic App, n8n flow, or a five-line shim, rather than baking Alertmanager's full schema into this tool.)

## Extending this

- Persist the alert buffer to Redis or a similar store so a restart doesn't drop in-flight alerts
- Add per-severity routing (e.g. critical alerts post immediately, warnings batch every 5 minutes)
- Feed `k8s-drift-detector` output into this tool so drift reports show up in the same digest as other operational alerts
