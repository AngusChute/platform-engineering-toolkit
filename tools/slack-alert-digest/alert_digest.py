#!/usr/bin/env python3
"""
slack-alert-digest

Aggregates noisy, high-frequency alerts (e.g. from Prometheus Alertmanager,
Grafana, or any webhook-based alerting source) into a single, readable Slack
digest instead of flooding a channel with one message per firing alert.

This solves a real operational problem: during a degraded period, a single
underlying issue (a stalled worker, a saturated disk) can fire dozens of
related alerts. A wall of individual Slack messages buries the signal that
matters under noise, makes it hard to triage at a glance, and trains
engineers to tune alerts out. Grouping by severity and source, and sending
one summary per digest interval, keeps the signal legible.

Usage:
    # Run as a long-lived process; collects alerts via /alert webhook,
    # flushes a digest to Slack every --interval seconds.
    python alert_digest.py --interval 300 --slack-webhook-url $SLACK_WEBHOOK_URL
"""

from __future__ import annotations

import argparse
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests
from flask import Flask, request, jsonify


@dataclass
class Alert:
    source: str
    severity: str
    summary: str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AlertBuffer:
    """Thread-safe buffer that collects alerts and periodically flushes a
    grouped digest to Slack."""

    SEVERITY_EMOJI = {
        "critical": "🔴",
        "warning": "🟡",
        "info": "🔵",
    }

    def __init__(self, slack_webhook_url: str, interval_seconds: int):
        self.slack_webhook_url = slack_webhook_url
        self.interval_seconds = interval_seconds
        self._alerts: list[Alert] = []
        self._lock = threading.Lock()

    def add(self, alert: Alert) -> None:
        with self._lock:
            self._alerts.append(alert)

    def _drain(self) -> list[Alert]:
        with self._lock:
            alerts, self._alerts = self._alerts, []
        return alerts

    def build_digest(self, alerts: list[Alert]) -> str | None:
        if not alerts:
            return None

        by_severity: dict[str, list[Alert]] = defaultdict(list)
        for alert in alerts:
            by_severity[alert.severity].append(alert)

        lines = [f"*Alert Digest — {len(alerts)} alert(s) in the last {self.interval_seconds // 60} min*\n"]

        # Show most severe first
        for severity in ("critical", "warning", "info"):
            severity_alerts = by_severity.get(severity)
            if not severity_alerts:
                continue

            emoji = self.SEVERITY_EMOJI.get(severity, "⚪")
            lines.append(f"{emoji} *{severity.upper()}* ({len(severity_alerts)})")

            # Group identical summaries from the same source to avoid
            # repeating "disk usage high" 40 times for one flapping host
            grouped: dict[tuple[str, str], int] = defaultdict(int)
            for a in severity_alerts:
                grouped[(a.source, a.summary)] += 1

            for (source, summary), count in grouped.items():
                suffix = f" (x{count})" if count > 1 else ""
                lines.append(f"  • [{source}] {summary}{suffix}")

            lines.append("")

        return "\n".join(lines)

    def flush(self) -> None:
        alerts = self._drain()
        digest = self.build_digest(alerts)
        if digest is None:
            return

        response = requests.post(self.slack_webhook_url, json={"text": digest}, timeout=10)
        response.raise_for_status()

    def run_periodic_flush(self) -> None:
        while True:
            time.sleep(self.interval_seconds)
            try:
                self.flush()
            except requests.RequestException as exc:
                print(f"Failed to post digest to Slack: {exc}")


def create_app(buffer: AlertBuffer) -> Flask:
    app = Flask(__name__)

    @app.route("/alert", methods=["POST"])
    def receive_alert():
        payload = request.get_json(force=True)

        alert = Alert(
            source=payload.get("source", "unknown"),
            severity=payload.get("severity", "info"),
            summary=payload.get("summary", "(no summary provided)"),
        )
        buffer.add(alert)
        return jsonify({"status": "queued"}), 202

    @app.route("/healthz", methods=["GET"])
    def healthz():
        return jsonify({"status": "ok"}), 200

    return app


def main():
    parser = argparse.ArgumentParser(description="Aggregate alerts into periodic Slack digests")
    parser.add_argument("--interval", type=int, default=300, help="Digest flush interval, in seconds")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen for incoming alert webhooks")
    parser.add_argument("--slack-webhook-url", required=True, help="Slack incoming webhook URL")
    args = parser.parse_args()

    buffer = AlertBuffer(slack_webhook_url=args.slack_webhook_url, interval_seconds=args.interval)

    flush_thread = threading.Thread(target=buffer.run_periodic_flush, daemon=True)
    flush_thread.start()

    app = create_app(buffer)
    app.run(host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
