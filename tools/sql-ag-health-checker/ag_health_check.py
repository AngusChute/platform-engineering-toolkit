#!/usr/bin/env python3
"""
sql-ag-health-checker

Checks the health of a SQL Server Always On Availability Group: replica
synchronization state, database synchronization health, and failover
readiness. Designed to run on a schedule and feed into the same alerting
path as other tools in this repo (see ../slack-alert-digest).

Always On AGs fail in ways that don't always trip an obvious application
error first — a replica can silently fall behind, or a database can drop
into a NOT SYNCHRONIZING state, well before anyone notices a problem. This
tool surfaces that drift early, which matters most for synchronous-commit
AGs where the whole point is zero data loss on failover: a replica that's
quietly out of sync defeats that guarantee without anyone knowing.

Usage:
    python ag_health_check.py --server my-sql-host --ag-name MyAG
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

import pyodbc

# Replicas in any of these states require attention
UNHEALTHY_REPLICA_STATES = {"NOT SYNCHRONIZING", "DISCONNECTED", "RESOLVING"}
UNHEALTHY_DB_STATES = {"NOT SYNCHRONIZING", "NOT HEALTHY", "REVERTING", "INITIALIZING"}


@dataclass
class ReplicaHealth:
    replica_server: str
    role: str
    connected_state: str
    synchronization_health: str
    availability_mode: str

    @property
    def is_healthy(self) -> bool:
        return self.synchronization_health == "HEALTHY" and self.connected_state == "CONNECTED"


@dataclass
class DatabaseHealth:
    database_name: str
    replica_server: str
    synchronization_state: str
    is_suspended: bool

    @property
    def is_healthy(self) -> bool:
        return self.synchronization_state == "SYNCHRONIZED" and not self.is_suspended


# Queries against the dynamic management views that actually surface AG
# health — these are the same DMVs used to chase down replica-state and
# sync issues in production.
REPLICA_QUERY = """
SELECT
    ar.replica_server_name,
    ars.role_desc,
    ars.connected_state_desc,
    ars.synchronization_health_desc,
    ar.availability_mode_desc
FROM sys.dm_hadr_availability_replica_states ars
JOIN sys.availability_replicas ar
    ON ars.replica_id = ar.replica_id
JOIN sys.availability_groups ag
    ON ar.group_id = ag.group_id
WHERE ag.name = ?
"""

DATABASE_QUERY = """
SELECT
    dbcs.database_name,
    ar.replica_server_name,
    drs.synchronization_state_desc,
    drs.is_suspended
FROM sys.dm_hadr_database_replica_states drs
JOIN sys.availability_replicas ar
    ON drs.replica_id = ar.replica_id
JOIN sys.dm_hadr_database_replica_cluster_states dbcs
    ON drs.replica_id = dbcs.replica_id AND drs.group_database_id = dbcs.group_database_id
JOIN sys.availability_groups ag
    ON ar.group_id = ag.group_id
WHERE ag.name = ?
"""


def connect(server: str, database: str = "master", trusted_connection: bool = True,
            username: str | None = None, password: str | None = None) -> pyodbc.Connection:
    if trusted_connection:
        conn_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={server};DATABASE={database};Trusted_Connection=yes;"
            f"Encrypt=yes;TrustServerCertificate=yes;"
        )
    else:
        conn_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={server};DATABASE={database};UID={username};PWD={password};"
            f"Encrypt=yes;TrustServerCertificate=yes;"
        )
    return pyodbc.connect(conn_str, timeout=10)


def get_replica_health(conn: pyodbc.Connection, ag_name: str) -> list[ReplicaHealth]:
    cursor = conn.cursor()
    cursor.execute(REPLICA_QUERY, ag_name)
    return [
        ReplicaHealth(
            replica_server=row.replica_server_name,
            role=row.role_desc,
            connected_state=row.connected_state_desc,
            synchronization_health=row.synchronization_health_desc,
            availability_mode=row.availability_mode_desc,
        )
        for row in cursor.fetchall()
    ]


def get_database_health(conn: pyodbc.Connection, ag_name: str) -> list[DatabaseHealth]:
    cursor = conn.cursor()
    cursor.execute(DATABASE_QUERY, ag_name)
    return [
        DatabaseHealth(
            database_name=row.database_name,
            replica_server=row.replica_server_name,
            synchronization_state=row.synchronization_state_desc,
            is_suspended=bool(row.is_suspended),
        )
        for row in cursor.fetchall()
    ]


def format_report(ag_name: str, replicas: list[ReplicaHealth], databases: list[DatabaseHealth]) -> str:
    lines = [f"Availability Group health report: {ag_name}\n"]

    unhealthy_replicas = [r for r in replicas if not r.is_healthy]
    unhealthy_dbs = [d for d in databases if not d.is_healthy]

    lines.append("Replicas:")
    for r in replicas:
        status = "✅" if r.is_healthy else "🔴"
        lines.append(
            f"  {status} {r.replica_server} [{r.role}] — "
            f"connection={r.connected_state}, sync={r.synchronization_health}, mode={r.availability_mode}"
        )

    lines.append("\nDatabases:")
    for d in databases:
        status = "✅" if d.is_healthy else "🔴"
        suspended_note = " (SUSPENDED)" if d.is_suspended else ""
        lines.append(f"  {status} {d.database_name} on {d.replica_server} — {d.synchronization_state}{suspended_note}")

    if not unhealthy_replicas and not unhealthy_dbs:
        lines.append(f"\n✅ All {len(replicas)} replica(s) and {len(databases)} database(s) healthy.")
    else:
        lines.append(
            f"\n⚠️  {len(unhealthy_replicas)} unhealthy replica(s), {len(unhealthy_dbs)} unhealthy database(s)."
        )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Check SQL Server Always On Availability Group health")
    parser.add_argument("--server", required=True, help="SQL Server hostname or instance to connect to")
    parser.add_argument("--ag-name", required=True, help="Name of the Availability Group to check")
    parser.add_argument("--username", help="SQL auth username (omit to use Windows/trusted auth)")
    parser.add_argument("--password", help="SQL auth password (omit to use Windows/trusted auth)")
    args = parser.parse_args()

    trusted = args.username is None

    conn = connect(args.server, trusted_connection=trusted, username=args.username, password=args.password)

    try:
        replicas = get_replica_health(conn, args.ag_name)
        databases = get_database_health(conn, args.ag_name)
    finally:
        conn.close()

    report = format_report(args.ag_name, replicas, databases)
    print(report)

    has_issues = any(not r.is_healthy for r in replicas) or any(not d.is_healthy for d in databases)
    sys.exit(1 if has_issues else 0)


if __name__ == "__main__":
    main()
