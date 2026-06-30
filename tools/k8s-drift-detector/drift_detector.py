#!/usr/bin/env python3
"""
k8s-drift-detector

Compares the desired state of Kubernetes manifests (as declared in Git) against
the live state of a cluster, and reports any drift — resources that have been
manually modified outside of the GitOps pipeline.

This is meant to run on a schedule (cron, GitHub Actions, or a Kubernetes
CronJob) as a safety net alongside Flux/ArgoCD: GitOps controllers correct
drift automatically, but knowing *that* drift happened — and who/what caused
it — is valuable for auditing and incident review.

Usage:
    python drift_detector.py --manifests-dir ./gitops-flux/apps/production --namespace default
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class DriftResult:
    kind: str
    name: str
    namespace: str
    differences: list[str] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return len(self.differences) > 0


def load_desired_manifests(manifests_dir: Path) -> list[dict]:
    """Load all YAML manifests from a directory (recursively), skipping
    Kustomize control files which aren't applyable resources themselves."""
    manifests = []
    skip_files = {"kustomization.yaml", "kustomization.yml"}

    for path in sorted(manifests_dir.rglob("*.yaml")):
        if path.name in skip_files:
            continue
        with open(path) as f:
            for doc in yaml.safe_load_all(f):
                if doc and "kind" in doc:
                    manifests.append(doc)

    return manifests


def get_live_resource(kind: str, name: str, namespace: str) -> dict | None:
    """Fetch the live state of a resource from the cluster via kubectl."""
    try:
        result = subprocess.run(
            ["kubectl", "get", kind, name, "-n", namespace, "-o", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError:
        return None


def compare_resource(desired: dict, live: dict) -> list[str]:
    """Compare a small, opinionated set of fields likely to indicate
    meaningful drift, rather than diffing the entire object (which would
    flag every Kubernetes-managed field like resourceVersion or status)."""
    differences = []

    kind = desired.get("kind", "")
    desired_spec = desired.get("spec", {})
    live_spec = live.get("spec", {})

    if kind == "Deployment":
        d_replicas = desired_spec.get("replicas")
        l_replicas = live_spec.get("replicas")
        if d_replicas is not None and d_replicas != l_replicas:
            differences.append(f"replicas: desired={d_replicas} live={l_replicas}")

        d_containers = desired_spec.get("template", {}).get("spec", {}).get("containers", [])
        l_containers = live_spec.get("template", {}).get("spec", {}).get("containers", [])

        d_images = {c["name"]: c.get("image") for c in d_containers}
        l_images = {c["name"]: c.get("image") for c in l_containers}

        for name, desired_image in d_images.items():
            live_image = l_images.get(name)
            if desired_image != live_image:
                differences.append(
                    f"container '{name}' image: desired={desired_image} live={live_image}"
                )

    return differences


def run(manifests_dir: Path, namespace: str) -> list[DriftResult]:
    manifests = load_desired_manifests(manifests_dir)
    results = []

    for manifest in manifests:
        kind = manifest["kind"]
        name = manifest["metadata"]["name"]
        manifest_namespace = manifest.get("metadata", {}).get("namespace", namespace)

        live = get_live_resource(kind, name, manifest_namespace)

        if live is None:
            results.append(
                DriftResult(
                    kind=kind,
                    name=name,
                    namespace=manifest_namespace,
                    differences=["resource not found in cluster"],
                )
            )
            continue

        differences = compare_resource(manifest, live)
        results.append(
            DriftResult(kind=kind, name=name, namespace=manifest_namespace, differences=differences)
        )

    return results


def main():
    parser = argparse.ArgumentParser(description="Detect drift between Git manifests and a live cluster")
    parser.add_argument("--manifests-dir", type=Path, required=True, help="Directory of Kubernetes YAML manifests")
    parser.add_argument("--namespace", default="default", help="Default namespace if not set in manifest")
    parser.add_argument("--json", action="store_true", help="Output results as JSON instead of human-readable text")
    args = parser.parse_args()

    results = run(args.manifests_dir, args.namespace)
    drifted = [r for r in results if r.has_drift]

    if args.json:
        print(json.dumps([r.__dict__ for r in drifted], indent=2))
    else:
        if not drifted:
            print(f"✅ No drift detected across {len(results)} resources.")
        else:
            print(f"⚠️  Drift detected in {len(drifted)}/{len(results)} resources:\n")
            for r in drifted:
                print(f"  {r.kind}/{r.name} (namespace: {r.namespace})")
                for diff in r.differences:
                    print(f"    - {diff}")
                print()

    sys.exit(1 if drifted else 0)


if __name__ == "__main__":
    main()
