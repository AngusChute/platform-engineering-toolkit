# tls-expiry-checker

A small Python tool that checks TLS certificate expiration across a list of hosts and flags anything approaching expiry at configurable thresholds.

## Why this exists

TLS expiry is one of the few outage causes that's entirely preventable — the certificate tells you exactly when it's going to fail, in advance, every time. The only way it turns into a 2 AM page is if nothing is actually watching for it. This is a deliberately small, focused version of the kind of expiry alerting I've built directly into production observability stacks (Grafana Cloud / Prometheus rules watching `probe_ssl_earliest_cert_expiry`), made standalone so it can run anywhere — a cron job, a CI pipeline step, a Kubernetes CronJob — without needing a full monitoring stack in place.

## How it works

The tool connects to each host over TLS *without* verifying the certificate chain — intentionally, since the goal is to report on whatever certificate is actually being presented, even an expired, self-signed, or otherwise invalid one, rather than fail silently on hosts with cert problems.

For each host it reports:
- Days remaining until expiry
- The exact expiry date
- Any connection/handshake errors (treated as their own category, separate from "expiring soon")

## Usage

```bash
pip install -r requirements.txt

cp hosts.example.txt hosts.txt
# edit hosts.txt with your real hosts

python tls_checker.py --hosts hosts.txt --warn-days 30 14 7
```

Example output:

```
⚠️  2 certificate(s) expiring within 30 days:
  🟡 internal-service.example.com:8443 — expires in 24 days (2026-07-24)
  🔴 legacy-api.example.com:443 — expires in 5 days (2026-07-05)

❌ 1 host(s) could not be checked:
  - unreachable-host.example.com:443 — [Errno 8] nodename nor servname provided, or not known
```

Exit code is `1` if anything is expiring within the warning window or unreachable, `0` if everything is healthy — making this easy to drop into a CI pipeline or cron job with alerting on failure:

```bash
python tls_checker.py --hosts hosts.txt --warn-days 14 --slack-webhook-url "$SLACK_WEBHOOK_URL"
```

When `--slack-webhook-url` is provided, the tool only posts to Slack if there's actually something to report — a clean run stays silent rather than adding to alert noise.

## Running on a schedule

As a Kubernetes CronJob:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: tls-expiry-checker
spec:
  schedule: "0 6 * * *"  # daily at 6am
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: tls-checker
              image: python:3.12-slim
              command: ["sh", "-c", "pip install -r requirements.txt && python tls_checker.py --hosts hosts.txt --warn-days 30 14 7 --slack-webhook-url $SLACK_WEBHOOK_URL"]
          restartPolicy: OnFailure
```

## Extending this

- Pull the host list dynamically from a source of truth (a Kubernetes Ingress list, a Terraform state file, a CMDB) instead of a static text file
- Add Prometheus metrics export (`days_remaining` as a gauge) for hosts you want graphed over time rather than just alerted on
- Add OCSP/revocation status checks alongside expiry
