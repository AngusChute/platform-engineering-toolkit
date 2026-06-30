# k8s-drift-detector

A small Python tool that compares Kubernetes manifests in Git against the live state of a cluster, and reports drift — resources that have been manually modified outside the GitOps pipeline.

## Why this exists

GitOps controllers like Flux or ArgoCD will *correct* drift automatically on their next reconciliation pass. But correcting drift silently isn't the same as knowing it happened. If someone scales a deployment manually during an incident, or a `kubectl edit` slips through during an outage, that's worth surfacing — for audit trails, for incident postmortems, and for catching it before the next reconciliation quietly reverts a change someone *thought* was permanent.

This tool is meant to complement a GitOps controller, not replace one: run it on a schedule, and treat its output as a signal worth investigating, not as the thing actually fixing the drift.

## What it checks

To keep the tool focused and avoid noisy false positives (Kubernetes mutates many fields on live objects — `resourceVersion`, `status`, defaulted fields — that don't represent real drift), it currently compares a deliberately narrow set of fields most likely to indicate meaningful, human-caused drift:

- Deployment replica counts
- Container image tags

This is intentionally extensible — see "Extending this" below.

## Usage

```bash
pip install -r requirements.txt

python drift_detector.py \
  --manifests-dir ../../gitops-flux/apps/production \
  --namespace default
```

Example output:

```
⚠️  Drift detected in 1/2 resources:

  Deployment/example-api (namespace: default)
    - replicas: desired=4 live=6
    - container 'example-api' image: desired=gcr.io/my-project/example-api:v1.4.0 live=gcr.io/my-project/example-api:v1.3.2
```

Exit code is `0` when no drift is found and `1` when drift is detected, making this easy to wire into CI or alerting:

```bash
python drift_detector.py --manifests-dir ./apps/production --json > drift-report.json || \
  curl -X POST -H 'Content-type: application/json' --data @drift-report.json "$SLACK_WEBHOOK_URL"
```

## Extending this

- Add checks for ConfigMaps/Secrets (hash comparison rather than full diff, since Secrets shouldn't be printed)
- Add a `--cluster-context` flag to check multiple clusters in one run
- Wire into the `slack-alert-digest` tool in this repo so drift reports show up in the same daily summary as other operational alerts
