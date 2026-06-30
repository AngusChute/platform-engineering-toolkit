# sql-ag-health-checker

A Python tool that checks the health of a SQL Server Always On Availability Group: replica synchronization state, database synchronization health, and connection status — surfaced from the same dynamic management views (DMVs) used to diagnose real AG incidents in production.

## Why this exists

Always On AGs are designed so that a primary failing over to a secondary is a non-event — but that guarantee only holds if the secondary was actually healthy and in sync at the moment of failover. A replica can quietly drift into `NOT SYNCHRONIZING`, or a single database within an otherwise-healthy AG can fall behind, well before any application-level symptom shows up. For a synchronous-commit AG specifically, the entire point is zero data loss on failover — a replica that's silently out of sync defeats that guarantee without anyone knowing until the moment it matters most: during an actual failover.

This tool queries the relevant DMVs directly and reports a clear pass/fail per replica and per database, so that drift gets caught on a schedule rather than discovered during an incident.

## What it checks

**Per replica** (from `sys.dm_hadr_availability_replica_states`):
- Connection state (`CONNECTED` vs `DISCONNECTED`)
- Synchronization health (`HEALTHY`, `PARTIALLY_HEALTHY`, `NOT_HEALTHY`)
- Role (primary/secondary) and availability mode (synchronous/asynchronous commit)

**Per database** (from `sys.dm_hadr_database_replica_states`):
- Synchronization state (`SYNCHRONIZED`, `SYNCHRONIZING`, `NOT SYNCHRONIZING`, etc.)
- Suspended state — a database can be technically "connected" but suspended from data movement, which this flags separately

## Usage

```bash
pip install -r requirements.txt
```

Requires the [Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server) installed on the machine running this script.

With Windows/trusted authentication:
```bash
python ag_health_check.py --server sql-primary.internal --ag-name ProductionAG
```

With SQL authentication:
```bash
python ag_health_check.py --server sql-primary.internal --ag-name ProductionAG --username monitor_svc --password "$SQL_MONITOR_PASSWORD"
```

Example output:

```
Availability Group health report: ProductionAG

Replicas:
  ✅ sql-primary.internal [PRIMARY] — connection=CONNECTED, sync=HEALTHY, mode=SYNCHRONOUS_COMMIT
  🔴 sql-secondary.internal [SECONDARY] — connection=CONNECTED, sync=NOT_HEALTHY, mode=SYNCHRONOUS_COMMIT

Databases:
  ✅ OrdersDB on sql-primary.internal — SYNCHRONIZED
  🔴 OrdersDB on sql-secondary.internal — NOT SYNCHRONIZING

⚠️  1 unhealthy replica(s), 1 unhealthy database(s).
```

Exit code is `1` on any unhealthy replica or database, `0` if everything is healthy — suitable for a scheduled job with alerting on failure, similar to the other tools in this repo:

```bash
python ag_health_check.py --server sql-primary.internal --ag-name ProductionAG || \
  curl -X POST -H 'Content-type: application/json' \
    --data "{\"text\": \"AG health check failed — see job logs\"}" "$SLACK_WEBHOOK_URL"
```

## A note on least-privilege access

This tool only needs `VIEW SERVER STATE` permission to query the DMVs above — it does not need `sysadmin` or write access of any kind. When wiring this into a production monitoring account, grant the narrowest permission that satisfies the DMV queries rather than reusing a broader service account.

## Extending this

- Add a check for the AG listener's current primary, to detect unexpected failovers between scheduled runs
- Export results as Prometheus metrics (`ag_replica_healthy{replica="..."} 1|0`) for trending over time, rather than just point-in-time pass/fail
- Feed output into `slack-alert-digest` so AG health shows up alongside other operational alerts in one place
