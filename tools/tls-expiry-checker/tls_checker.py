#!/usr/bin/env python3
"""
tls-expiry-checker

Checks the TLS certificate expiration date for a list of hostnames and
reports anything approaching expiry, at configurable warning thresholds.

This mirrors a class of alert I've built directly into production
observability stacks (Grafana Cloud / Prometheus) — TLS expiry is one of
those failure modes that's entirely preventable, but only if something is
actually watching for it. A cert that silently expires on a Friday night
is a self-inflicted outage.

Usage:
    python tls_checker.py --hosts hosts.txt --warn-days 30 14 7
    python tls_checker.py --hosts hosts.txt --slack-webhook-url $SLACK_WEBHOOK_URL
"""

from __future__ import annotations

import argparse
import socket
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from cryptography import x509


@dataclass
class CertStatus:
    host: str
    port: int
    expires_at: datetime | None
    days_remaining: int | None
    error: str | None = None

    @property
    def is_ok(self) -> bool:
        return self.error is None


def check_host(host: str, port: int = 443, timeout: float = 5.0) -> CertStatus:
    """Open a TLS connection to host:port and read the peer certificate's
    notAfter date, without verifying the chain — we want to report on
    *any* presented cert, including ones that are otherwise invalid."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                der_cert = ssock.getpeercert(binary_form=True)
                cert = x509.load_der_x509_certificate(der_cert)

                expires_at = cert.not_valid_after_utc
                days_remaining = (expires_at - datetime.now(timezone.utc)).days

                return CertStatus(
                    host=host,
                    port=port,
                    expires_at=expires_at,
                    days_remaining=days_remaining,
                )
    except Exception as exc:  # noqa: BLE001 - we want to report any failure, not just specific types
        return CertStatus(host=host, port=port, expires_at=None, days_remaining=None, error=str(exc))


def parse_hosts_file(path: Path) -> list[tuple[str, int]]:
    """Parse a hosts file. Each line is `hostname` or `hostname:port`.
    Blank lines and lines starting with # are ignored."""
    hosts = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            host, port_str = line.rsplit(":", 1)
            hosts.append((host, int(port_str)))
        else:
            hosts.append((line, 443))
    return hosts


def format_report(results: list[CertStatus], warn_days: list[int]) -> str:
    warn_threshold = max(warn_days)
    lines = []

    errored = [r for r in results if not r.is_ok]
    expiring = [r for r in results if r.is_ok and r.days_remaining is not None and r.days_remaining <= warn_threshold]
    healthy = [r for r in results if r.is_ok and r not in expiring]

    if expiring:
        lines.append(f"⚠️  {len(expiring)} certificate(s) expiring within {warn_threshold} days:")
        for r in sorted(expiring, key=lambda r: r.days_remaining):
            urgency = "🔴" if r.days_remaining <= 7 else "🟡"
            lines.append(f"  {urgency} {r.host}:{r.port} — expires in {r.days_remaining} days ({r.expires_at:%Y-%m-%d})")
        lines.append("")

    if errored:
        lines.append(f"❌ {len(errored)} host(s) could not be checked:")
        for r in errored:
            lines.append(f"  - {r.host}:{r.port} — {r.error}")
        lines.append("")

    if healthy and not expiring and not errored:
        lines.append(f"✅ All {len(healthy)} certificate(s) healthy.")

    return "\n".join(lines) if lines else "✅ All certificates healthy."


def main():
    parser = argparse.ArgumentParser(description="Check TLS certificate expiry for a list of hosts")
    parser.add_argument("--hosts", type=Path, required=True, help="Path to a file listing hosts (one per line, optionally host:port)")
    parser.add_argument("--warn-days", type=int, nargs="+", default=[30, 14, 7], help="Day thresholds to flag as approaching expiry")
    parser.add_argument("--slack-webhook-url", help="Optional Slack webhook URL to post the report to")
    args = parser.parse_args()

    hosts = parse_hosts_file(args.hosts)
    results = [check_host(host, port) for host, port in hosts]

    report = format_report(results, args.warn_days)
    print(report)

    has_issues = any(not r.is_ok or (r.days_remaining is not None and r.days_remaining <= max(args.warn_days)) for r in results)

    if args.slack_webhook_url and has_issues:
        try:
            requests.post(args.slack_webhook_url, json={"text": report}, timeout=10).raise_for_status()
        except requests.RequestException as exc:
            print(f"Failed to post report to Slack: {exc}", file=sys.stderr)

    sys.exit(1 if has_issues else 0)


if __name__ == "__main__":
    main()
